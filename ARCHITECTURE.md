# Architecture

Why this service exists, how it is built, and what has been done so far.

> **Maintenance rule:** this document is updated in the same change as
> the code. A change is not finished until it is reflected here. Record
> decisions with their *rationale and evidence*, not just the resulting
> structure — the code already shows the structure.

---

## 1. Why

TugrikRate is an iOS currency converter for the Mongolian tögrög. It
needs current exchange rates from Mongolian banks, and it must never
call those banks directly:

- **The banks have no public API.** Ten of the fifteen sources are
  undocumented JSON endpoints that the banks' own websites call; five
  have no endpoint at all and must be scraped from a rendered page.
  Both can change without notice. That breakage belongs in one service
  we control, not in an app binary that takes days to ship a fix.
- **Fifteen sources, fifteen shapes.** Every bank names its fields
  differently, and several are actively misleading (see §5). Doing
  that normalisation on-device would mean fifteen parsers in Swift and
  a new app release every time a bank renames a key.
- **Rate precision is not negotiable.** A currency app that rounds is
  a currency app that lies. Exactness has to be preserved end to end,
  and JSON numbers are doubles in every client — so the wire format is
  part of the correctness argument, not a detail.
- **Crawling has to be polite and observable.** One scheduler with
  jitter and per-source failure tracking, rather than every installed
  app hammering fifteen bank websites.

The service therefore owns: collection, normalisation, history, and one
stable contract. The app owns: display.

## 2. Lineage

Forked from
[btseee/mongolian-bank-exchange-rate](https://github.com/btseee/mongolian-bank-exchange-rate)
(MIT, Battseren Badral). All 15 crawlers originate there.

The fork keeps `app/crawlers/` deliberately close to upstream so its
"the bank renamed a JSON key" fixes cherry-pick cleanly. Everything a
crawler *claims* — which channel a number belongs to, which side is
which, what unit it is quoted in — was moved out into
`app/sources/registry.py`, a file upstream never touches and therefore
never conflicts on.

Six crawlers are deliberately diverged because their upstream behaviour
was wrong: `mongolbank`, `capitronbank`, `transbank`, `mbank`,
`sendmn`, `naimansharga`. Each carries a module docstring explaining
what changed and why. **Keep the divergence when resolving merges.**

## 3. The three invariants

Most of this codebase exists to protect these. Breaking one is a
correctness bug, not a style question.

1. **Exactness.** Rates never touch binary floating point. Crawlers
   decode with `json_exact` (`json.loads(..., parse_float=Decimal)`)
   so numbers are never floats even momentarily, parse via
   `app/utils/decimals.parse_decimal`, store as decimal strings, and
   serve as JSON strings.
2. **Only real channels.** A quote's `channel`/`side` must be something
   the source actually publishes. Nothing is copied between channels,
   nothing falls back from one to another, nothing is inferred from
   what a bank "probably" means. A source that does not say gets
   `channel: "unspecified"`.
3. **Missing is missing.** `None`, `""`, `"-"`, `0` mean the source
   does not publish that number. They never become a value, and a
   missing quote is *absent* from the response rather than null.

## 4. How it is built

### Flow

```
                      app/services/scheduler.py
                      APScheduler, Asia/Ulaanbaatar, jitter
                                  │
                                  ▼
                      app/services/collector.py
                      per-source: own session, own try/except
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                   ▼
      app/crawlers/<bank>.py              (failure path)
      fetch + parse, upstream shape                 │
      returns {cash:{buy,sell},                     ▼
               noncash:{buy,sell}}         record_failure()
                │                          streak++, last good
                ▼                          snapshot untouched
      app/sources/adapter.py
      reads ONLY the slots the registry
      declares real → list[Quote]
                │
                ▼
      app/sources/payload.py
      canonical bytes → payload_hash
                │
                ▼
      app/db/snapshots.py
      hash changed? INSERT : bump last_checked_at
                │
                ▼
      app/api/routers/v1.py
      GET /v1/rates — the frozen contract
```

### Module responsibilities

| Path | Responsibility |
| --- | --- |
| `app/crawlers/` | Fetch and parse one bank. Upstream-shaped; keep mergeable. |
| `app/sources/registry.py` | **Single source of truth** for what each source publishes, plus the evidence for every claim. |
| `app/sources/adapter.py` | Crawler output → `Quote` objects. Where invariant 2 is enforced. |
| `app/sources/payload.py` | Canonical payload bytes and the change-detection hash. |
| `app/sources/models.py` | `Quote`, `CrawlResult`, channel/side constants. |
| `app/services/collector.py` | Runs crawls, persists, isolates failures. |
| `app/services/scheduler.py` | Cron triggers per cadence class and source group. |
| `app/services/freshness.py` | Interval + `ok`/`stale`/`failing` rules, shared by scheduler and API so they cannot drift. |
| `app/db/snapshots.py` | Insert-on-change, bump-on-no-change, failure state. |
| `app/models/snapshot.py` | `sources`, `rate_snapshots`, `source_state` schema. |
| `app/api/routers/v1.py` | The public contract. Treat as frozen. |
| `app/utils/decimals.py` | Locale-independent exact parsing. |

### Data model

- **`sources`** — the registry mirrored into the DB, so snapshots keep
  meaning if a source is later retired from code.
- **`rate_snapshots`** — `source_id`, `fetched_at`, `published_at`
  (nullable), `last_checked_at`, `payload_hash`, `quotes` (JSON). One
  row per *distinct* payload.
- **`source_state`** — failure streak, last error, last success. Kept
  off the snapshot table deliberately: a failing crawl must not
  disturb the last good snapshot.

### Cadence

All Asia/Ulaanbaatar, because banks republish on their own working day.

| Group | 08:00–20:00 | Otherwise |
| --- | --- | --- |
| 10 JSON sources (`fast`) | every 15 min | hourly |
| 5 Playwright sources (`slow`) | hourly | every 4 h |

Every trigger carries ±`CRAWL_JITTER_SECONDS` so no bank sees an exact
interval boundary. `CRAWL_GROUP` (`all`/`fast`/`slow`) lets the
Playwright five run in a separate process (`scripts/worker.py`), so a
Chromium OOM cannot take the API down with it. The groups own disjoint
sources, so the two processes need no coordination —
`tests/test_scheduler.py::TestSourceGroups` asserts that partition.

## 5. Key decisions and the evidence behind them

Recorded because none of this is recoverable from the code alone.

### Why channel labels were not taken from field names

Reading live payloads instead of trusting field names found four bugs
that were shipping wrong rates:

| Source | What was wrong | Evidence |
| --- | --- | --- |
| Capitron | Returns 3 rows per currency keyed by `rtypecode` (1 reference, 2 cash, 3 non-cash). Upstream's loop overwrote per currency, so the last row won and was copied into both channels — publishing the **non-cash rate as cash** and discarding the real cash rate. | State Bank's explicitly-labelled rows the same day show identical spreads: cash 26, non-cash 8. Capitron's type 2 = 3588/3614 (26), type 3 = 3588/3596 (8). |
| TransBank | Labels sides from the **customer's** perspective — `BUY_RATE` 3617 is what you pay. Taken literally it made TransBank look like the best buy rate in the country. | Every other source quotes bank-side (State Bank cash 3589/3615). After swapping, TransBank reads 3587/3617 (spread 30) and 3587/3596 (9), matching. |
| Bank of Mongolia | One official rate per currency was duplicated into `noncash.buy` and `noncash.sell`, inventing a zero-width spread the central bank never published. | It is a reference rate; there is no spread to publish. |
| M Bank, SendMN, Naiman Sharga | One unlabelled buy/sell pair copied into both channels. | Payloads state no channel anywhere. Reported as `unspecified` rather than guessed from business type. |

`app/sources/adapter.py` also flags `buy > sell`, which is how the
TransBank inversion would be caught if it recurred — no bank pays more
than it charges.

### Why `unit_basis` is 1 everywhere, and what is `verified: false`

`scripts/probe_units.py` anchors every currency against that source's
*own* USD rate, so no external reference is needed. Result: every fiat
currency confirmed at basis 1 across all sources. JPY spans 20.17–23.14
over 13 independent sources; a per-10 quote would read ~226, an
unmissable order of magnitude away.

Not confirmed, therefore `verified: false`:
- **XAU/XAG** and the non-ISO spellings banks use (`ZAU`/`ZAG` at
  Capitron, `AUG`/`AGG` at TDBM). Golomt quotes XAU at 15,312,000 and
  XacBank at 478,826 — a 32× gap, i.e. troy ounce vs gram. That is a
  real unit difference but not a power of ten, so `unit_basis` cannot
  express it honestly.
- **KPW**, whose anchor is itself disputed (~130 vs ~900 per USD).

`MNT` self-quotes (several sources publish MNT = 1) are dropped.

### Why source names are evidence-based

Every source carries an official English name (`name`) and Mongolian
Cyrillic name (`name_mn`), and `name_evidence` in the registry records
where each came from. Both names are served on `GET /v1/rates` (and
`/v1/rates/{id}/history`); `/v1/sources` also serves the evidence.
`tests/test_source_names.py` pins the table, so a name cannot drift
without someone touching the evidence.

Researched 2026-09-29 by web search of each bank's own pages (site
titles, official Facebook pages, regulator/lender records). **The bank
sites were unreachable from the sandbox that session** (egress proxy),
so nothing was read off the live sites themselves - recheck against
them when possible. Corrections made to the earlier registry values:

| Source | Was | Now | Why |
| --- | --- | --- | --- |
| TransBank | `Trans Bank` / `Транс Банк` | `TransBank` / `Тээвэр Хөгжлийн Банк` | transbank.mn is titled *Тээвэр хөгжлийн банк* ("Transport Development Bank"); `Транс Банк` was a phonetic rendering of the brand, not a registered name. |
| State Bank | `State Bank` | `State Bank of Mongolia` / `Төрийн банк` | The bank's own Facebook page; `State Bank` was only Wikipedia's article title. |
| TDB | `Trade and Development Bank` | `Trade and Development Bank of Mongolia` | tdbm.mn/en and ADB both use the full form. |
| National Investment Bank | `National Investment Bank` | `National Investment Bank of Mongolia` | The bank's Facebook page and its SWIFT record. |
| XacBank | `Хас Банк` | `ХасБанк` | The bank writes it as one word. Mongolian sources vary (`Хас банк` on mn.wikipedia). |
| M Bank | `М Банк` | `М банк` | m-bank.mn page titles. |
| Naiman Sharga | `Найман Шарга` | `Найман шарга валют арилжаа` | Its own page name. **No official English name exists**; `Naiman Sharga` is a transliteration. |
| SendMN | `SendMN` | `Сэнд Эм Эн ББСБ` | Legal Mongolian name (SendMN NBFI LLC). Consumer brand is `SendMN` in both languages, so the app may prefer the English string for display. |

Unchanged after checking: Khan Bank, Golomt Bank, Arig Bank, Bank of
Mongolia, Capitron Bank, Bogd Bank, Chinggis Khaan Bank. Where several
registered forms exist (Bogd: "of Mongolia" / JSC / Llc) the short
brand is used and the variants are listed in `name_evidence`.

### Why the payload hash is canonical, not raw

Change detection keys on `payload_hash`. Hashing the raw HTTP body
fails: rendered pages carry build ids, CSRF tokens and analytics that
change on every single load, so every crawl would insert a snapshot
forever. Some JSON APIs have the same problem — Capitron ships a
growing `histories` array with `created` timestamps, SendMN a `trend`
indicator, Naiman Sharga `avahChange`/`zarahChange`.

So the hash is taken over a canonical form: JSON re-serialised with
sorted keys, with each source's declared `volatile_keys` stripped at
any depth. Playwright crawlers `record_payload()` only the extracted
rate rows, never the page.

### Why rates are stored as strings, not NUMERIC

SQLite has no exact numeric type; SQLAlchemy routes `Numeric` through
float there and warns about it. Decimal strings round-trip identically
on both SQLite and Postgres, and are exactly what the API serves — so
there is no conversion step anywhere that could reintroduce a float.

### Why the scheduler is in-process

A 15-minute cadence is below what an external HTTP trigger (GitHub
Actions cron) holds reliably. `SCHEDULER_ENABLED=false` hands control
back to `POST /api/admin/crawl` for deployments that prefer it.

### Why there is no backfill

Banks do not serve historical intraday rates. Under snapshot semantics
a backfill would write invented history behind real timestamps.

### Why the legacy table was not migrated

`currency_rates` is left in place but unused. Its rows carry the
labelling errors above — importing them would put known-wrong data
behind real timestamps. Drop it by hand when satisfied.

## 6. What is done so far

**v2.0.0 — 2026-09-29.** Complete rebuild from the fork.

- [x] `rate_snapshots` history; insert on payload-hash change, bump
      `last_checked_at` otherwise. Verified live: a second crawl of all
      15 sources produced 0 new rows.
- [x] Exact `Decimal` end to end; stored and served as strings.
      Locale-independent parser; malformed grouping such as
      `"3450.50.50"` reports missing instead of becoming 34,505,050.
- [x] Real channels only; `unspecified` for the three unlabelled
      sources; `reference` for the Bank of Mongolia.
- [x] `unit_basis` with `verified` flag, evidence-based.
- [x] `GET /v1/rates` with `status`, ETag/304, filtering;
      `/v1/sources`; `/v1/rates/{id}/history`.
- [x] APScheduler cadence with jitter; `CRAWL_GROUP` worker split.
- [x] Per-source crawl isolation.
- [x] Khan Bank TLS fix (was failing 100%); Naiman Sharga multi-day
      lookback (2–3 day publishing gaps).
- [x] 186 tests; isort/black/ruff clean; CI green on Python 3.14.
- [x] Public repo `nmka55/tugrikrate-backend`; ghcr.io image published.
- [x] Running locally against SQLite, seeded 15/15.

**2026-09-29 (later) - source names.**

- [x] Official English + Mongolian names researched and evidenced for
      all 15 sources; `name_mn` added to `GET /v1/rates`
      (additive; `schema_version` stays 1). See §5.
- [x] 235 tests; isort/black/ruff clean.

**Requested but NOT built yet - blocked on network access.** Asked for
in the same session: (1) international sources Frankfurter and
ExchangeRate-API, normalised into the existing feed format; (2) source
logos crawled and served as image URLs. Both need to *read live
payloads*, which invariant-first practice here demands before any
crawler is written (§5: four bugs were found only by reading payloads).
The sandbox egress proxy returned 403 for `api.frankfurter.dev`,
`open.er-api.com`, `frankfurter.dev`, `exchangerate-api.com` and all
15 bank sites. Nothing below has been verified against a live response.

Researched so far, **secondhand** (search snippets, plus the Frankfurter
maintainer's GitHub reply, which was read directly):

| | Frankfurter | ExchangeRate-API |
| --- | --- | --- |
| Key needed | No | Open-access endpoint `open.er-api.com`: no. Registered free key: yes |
| Daily cap | **None.** Maintainer, discussion #42: "Short answer, there are no limits." Abuse rate-limiting only | Open endpoint is rate-limited with no stated daily number. Docs: request once per 24 h and you never need worry; hourly "and never get rate limited". Registered free key: 1,500 calls/**month** (~48/day) |
| Data refresh | Central-bank daily sources (ECB et al.) | Once per day (open endpoint) |
| Currencies | Fewer (ECB-derived core set; the repo now also mentions a `/v2` API with more providers) | 160+, MNT expected |
| Unknowns | **Whether MNT is published at all** - unread. Whether `/v1` or `/v2` is current | Response shape, `time_next_update_unix` semantics, attribution terms |

Decisions already made for when this is built (so they are not
re-litigated):

- Neither is a bank quote. Each publishes a single mid/reference rate,
  so quotes are `channel: "reference"`, `side: "reference"` - the same
  as the Bank of Mongolia (invariants 2 and 3). No buy/sell spread is
  ever synthesised.
- A rate MNT does not appear in the source's own payload is **not**
  derived by crossing through USD. That would be inference, not
  publication. If Frankfurter carries no MNT, it ships with no MNT
  quotes rather than computed ones.
- Both must use `json_exact`/`parse_decimal` - no floats - and their
  payload hash must strip volatile keys.
- Call budget is a hard guard in code, not only a cron cadence: a
  per-source daily ledger that refuses to fetch past a configured
  ceiling, and honours the source's own next-update time if it states
  one. Target: well under the limits above, e.g. ExchangeRate-API at
  most once every 6 h (4/day).
- Quote direction: MNT per 1 unit of foreign currency, matching every
  bank quote. Both APIs return "units of X per 1 base", so the
  inversion `1 / rate` must be done in `Decimal` with a fixed,
  documented precision and recorded in the source's `evidence`.
- Logos: fetched by a script/job, stored as static files we host and
  served by absolute URL - never hot-linked to the bank's site, whose
  URL can change. Not built.

**Unblock:** add these hosts to the environment's allowed domains (or
raise Network access): `api.frankfurter.dev`, `frankfurter.dev`,
`open.er-api.com`, `www.exchangerate-api.com`, and the 15 bank hosts
in `app/config.py` (plus `send.mn`, the current SendMN site).

**Not done / known gaps**

- [ ] **The iOS app is not connected yet.** No client exists.
- [ ] **No production deployment.** Local SQLite only. A real
      deployment needs Postgres (free tiers have no persistent disk)
      and a decision on the Playwright worker split.
- [ ] **CKBank tiered rates are collapsed.** It publishes two USD rows
      (`5000 хүртэл` / `5000-с дээш`); the contract has no tier
      dimension so the first wins. Adding tiers is a v2 contract change.
- [ ] **StateBank's legacy branch is unexercised.** The live payload
      uses the modern labelled shape; the legacy `BuyRate`/`SellRate`
      branch is kept for resilience but its channel is unconfirmed.
- [ ] **No auth or quota on `/v1/rates`.** Public, rate-limited
      in-process only. Fine for one app; revisit before wider use.
- [ ] **Single-process assumptions.** Rate limiter and admin job lock
      are in-memory; running two replicas of the same `CRAWL_GROUP`
      would double every crawl.

## 7. Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python -m scripts.migrate_v1                 # schema
python main.py                               # seed: one full crawl
python -m uvicorn app.api.api:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters if a physical iPhone needs to reach it over
the LAN. Full check sequence before any commit:

```bash
isort app tests scripts main.py --check-only && black app tests scripts main.py --check
ruff check app tests scripts main.py
pytest
```
