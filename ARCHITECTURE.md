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
| `app/crawlers/frankfurter.py` | International reference rates (not a bank). One request per currency; own call budget. Not in `HTTP_CRAWLERS`, so `crawlers/__init__.py` stays identical to upstream. |
| `app/utils/call_budget.py` | Hard per-UTC-day ceiling on outbound requests; the scheduler refuses to start a cadence that would exceed it. |
| `app/sources/logos.py` + `app/static/logos/` | Logo files and `manifest.json` (hash, size, provenance). Read at request time; nothing is fetched at request time. |
| `scripts/fetch_logos.py` | Refreshes the logos and the manifest. Verifies each App Store publisher. |
| `scripts/export_openapi.py` → `docs/openapi.json` | Committed contract snapshot; `tests/test_openapi.py` fails when stale. |

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
| Frankfurter (`daily`, HTTP side) | every `INTL_CRAWL_INTERVAL_HOURS` (12) around the clock, ~324 requests/day | |

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

### Why Frankfurter is built the way it is

Everything below was read off the live API on 2026-09-29, not assumed:

- **`/v1` cannot be used.** It is ECB-only (30 currencies) and answers
  `{"message":"not found"}` for MNT. `/v2` lists 166 currencies and
  serves MNT. Base URL `https://api.frankfurter.dev/v2`.
- **One request per currency.** `/v2/rates?base=X&quotes=MNT` returns
  `[{"date","base","quote","rate"}]` - MNT per 1 X, the feed's own
  format. `base` takes a single currency (`base=EUR,USD` is a 422), and
  inverting `base=MNT` is useless: the source rounds to ~5 significant
  digits, so MNT→USD returns `0.00028`. Its direct KZT→USD is
  `0.00227` (3 digits); KZT→MNT is `8.1477`. That is why the feed
  carries **MNT per unit for every currency** and lets the app divide
  for foreign↔foreign conversion: it is more precise than any direct
  pair the source offers.
- **Reference channel, no spread.** One blended mid figure per pair, no
  buy/sell, no channel → `reference`/`reference`, exactly like the Bank
  of Mongolia (invariants 2 and 3). Nothing is derived or crossed
  through USD.
- **It is not an independent market rate.** Rates are blended across
  the central banks that publish the pair (`expand=providers` shows the
  contributors). For USD/MNT they are BDI, BOM, CBKKW, CBR, CBU, NBK,
  NBKR, NBP; BDI, BOM and CBR carry the Bank of Mongolia's own figure
  (3595.17), NBP's is six days old (3632.14). Blend 3594.95. Treat it
  as "what central banks say", tracking our Bank of Mongolia source.
  The blend moves during the day as providers publish (EUR read 4093.69
  and 4093.55 an hour apart).
- **Excluded:** MNT itself and the four metals (XAU/XAG/XPD/XPT), which
  the banks already publish and quote in inconsistent units.
  Currencies whose catalogue entry ended >7 days ago are skipped.
  161 currencies are served.
- **Failure policy.** 404 / unpublished pair → skipped with a warning.
  HTTP 429 and call-budget exhaustion → abort the crawl at once. More
  than `INTL_MAX_FAILED_PERCENT` (10%) failing → the whole crawl fails
  and the last good snapshot stays, rather than publishing holes.
  Each response row is checked to be the pair that was asked for.
- **Call budget.** Frankfurter documents no quota ("no monthly or daily
  caps", only abuse rate-limiting), so the limit is self-imposed:
  `INTL_DAILY_CALL_LIMIT` (1000). A crawl costs 162 requests (1
  catalogue + 161 pairs); at 12 h that is ~324/day. `DailyCallBudget`
  refuses the request that would cross the ceiling, and
  `build_scheduler` raises `ValueError` at startup if the configured
  interval would plan more than the ceiling (`INTL_CRAWL_INTERVAL_HOURS=1`
  is rejected). A live crawl takes ~84 s from the sandbox (150 ms pause
  between requests).
- **Freshness.** Its stated date is the newest date across pairs.
  `published_stale_hours=96` (not the banks' 36) because providers do
  not publish at weekends. Status turns stale after 3 missed crawls
  (36 h).
- **Licence.** Free for commercial use, but "the rates themselves fall
  under each provider's terms". Not audited per provider; the Russian
  and Kuwaiti central banks publish terms/disclaimers. Revisit before a
  public launch.

### Why ExchangeRate-API was rejected

Evaluated and **not built**, by decision of the project owner. Its Terms
(exchangerate-api.com/terms) say the licence "does not permit
re-distribution of our data" and that it "may only be used for your end
purposes and not in any product or service that offers programmatic or
automatic access to exchange rate data"; the open endpoint also
requires an attribution link. `GET /v1/rates` *is* a programmatic
rates API, so serving their data through it would likely breach the
Terms. Facts kept for whoever revisits it: open endpoint
`open.er-api.com/v6/latest/{BASE}`, no key, 166 currencies incl. MNT,
updates once per 24 h and states `time_next_update_unix`, rate-limited
(429, 20-minute cooldown) with hourly requests "never rate limited";
registered free key 1.5k requests/month. A replacement API is being
sought by the owner - anything added must pass the same test: does its
licence allow republishing through an API?

### Why logos are hosted copies with recorded provenance

- **Hosted, not hot-linked.** Bank sites change URLs without notice and
  some block by IP (Khan Bank returns an "Access Denied" page to
  datacenter addresses). `scripts/fetch_logos.py` downloads once into
  `app/static/logos/`; `manifest.json` records file, SHA-256, size,
  dimensions, origin URL and page, publisher and retrieval time.
- **App Store icon first.** Each institution's own iOS app icon is a
  uniform 512×512 raster - what a list row wants, and UIImage cannot
  load SVG from a URL (several banks' site logos are SVG-only, wide
  wordmarks, or 16-px favicons). The App Store lookup's `sellerName`
  must contain the expected publisher or the script fails, so an app id
  taken over by someone else cannot silently ship the wrong logo.
  Twelve logos come from there; Bank of Mongolia (its
  apple-touch-icon), CK Bank (site icon, 128 px) and Frankfurter (its
  declared 512-px icon) from their own sites.
- **Naiman Sharga has no logo, on purpose.** Its only app is published
  by an individual, and its Wix site declares no icon of its own.
  Provenance cannot be established, so `logo_url` is `null` (invariant
  3). It is pinned in `tests/test_logos.py` (`NO_LOGO`).
- **Capitron discrepancy.** Its site favicon (32 px) is a different mark
  from its app icon. The app icon is used; noted in the manifest.
- **Serving.** `logo_url` is absolute: `PUBLIC_BASE_URL` if set, else
  the request origin - **set it in production**, or behind a TLS proxy
  the app may be handed `http://`. The URL embeds `?v=<sha256[:12]>`
  and is served with `Cache-Control: max-age=604800`, so a replaced
  logo is a new URL. `/static/` is exempt from the 60/min rate limit
  (16 images fetched at first launch would starve `/v1/rates`); the
  ETag hashes the logo path, not the host.
- **Trademarks.** The logos are the institutions' marks, shown to
  identify them (nominative use). They are not covered by the repo's
  MIT licence.

### OpenAPI

FastAPI generates OpenAPI 3.1 at `/openapi.json` (Swagger UI at `/`).
**The mobile app does not need it to keep working** - it decodes JSON
with `Codable` at runtime, and nothing consults the spec. Its value is
(a) generating Swift models (swift-openapi-generator) instead of
hand-writing DTOs and (b) review: `docs/openapi.json` is committed and
`tests/test_openapi.py` fails when it drifts, so a wire-format change
cannot land unnoticed. To make generated types correct, `schema_version`
has no default (a default marks it optional → `Int?`), `/v1/rates`
documents `ETag` and the 304, and `/v1/sources` and history now have
response models. Runtime type mismatches are still guarded by the
tolerant-decoding rules in `docs/mobile-integration-prompt.md`
(non-exhaustive enums, `Decimal` from strings, nullable `logo_url`).

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

**2026-09-29 (later still) - international source, logos, OpenAPI.**
Network access was opened mid-session, so payloads could finally be
read; nothing above was assumed.

- [x] **Frankfurter** as source #16 (`international_aggregator`,
      reference channel, 161 currencies, MNT per unit). Verified end to
      end against the live API: crawl → snapshot → `/v1/rates`, hash
      stable on re-crawl, 304 works. Call budget + startup guard.
- [x] **Logos** for 15 of 16 sources (`logo_url`), with provenance
      manifest and publisher verification. Naiman Sharga: none, see §5.
- [x] **OpenAPI** snapshot + drift test; remaining v1 endpoints typed.
- [x] Rejected ExchangeRate-API (licence) - see §5.
- [x] 314 tests; isort/black/ruff clean.

**Not done / known gaps**

- [ ] **The iOS app is not connected yet.** No client exists.
- [ ] **No production deployment.** Local SQLite only. A real
      deployment needs Postgres (free tiers have no persistent disk)
      and a decision on the Playwright worker split.
- [ ] **A second international source is wanted** (ExchangeRate-API
      rejected). Must allow republishing through an API.
- [ ] **Frankfurter licence not audited per provider** (§5).
- [ ] **Bank sites were spot-checked, not all crawled from the sandbox.**
      Khan Bank blocks datacenter IPs and NIB's certificate chain does
      not verify here; their *crawlers* are untested in this sandbox.
      Logos for them came from the App Store.
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
