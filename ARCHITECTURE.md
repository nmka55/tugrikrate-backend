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

## Conversion: what the backend does and does not do

**Owner's decision (2026-09-29): all conversion logic, including the
fallback between sources, lives in the mobile app.** The backend does
not convert, has no `/convert` endpoint, and does not specify or test
the fallback order. The app-side rules (which source to use when MNT
is involved, in which order, how to label the result) are written in
one place only: `docs/mobile-integration-prompt.md`, rule 11. Do not
copy them back here; if they change, they change there.

What the backend owns is the **inputs**, and one rule constrains them:

**USD is the pivot - confirmed by the owner.** The server downloads
*only* USD-based rates from foreign sources: one table of "units of X
per 1 USD". A foreign pair is computed by the app as **X → USD → Y**.
Rates are never downloaded per pair: a table for every pair on the
planet is ~160² ≈ 25,600 rates - infeasible to fetch, store or keep
fresh, and (measured) *less* precise than the pivot anyway (§5, "Why
Frankfurter is a USD table").

The backend therefore serves two feeds, kept apart because their
`rate` means different things:

| Endpoint | Sources | `rate` means |
| --- | --- | --- |
| `GET /v1/rates` | the 15 Mongolian sources | MNT per `unit_basis` units |
| `GET /v1/fx` | international (Frankfurter; fxRatesAPI only with `X-App-Key`) | units of `currency` per 1 USD, no MNT |

Consequences for the backend:

- A foreign source is acceptable only if it serves the whole USD table
  in **one request**; anything needing a request per currency or per
  pair breaks the 4-a-day ceiling.
- Foreign sources never contribute MNT rates: Frankfurter's MNT row is
  dropped (MNT comes from Mongolian sources, and Frankfurter's MNT is
  largely a copy of the Bank of Mongolia's anyway, §5).
- The two feeds are never merged into one response
  (`tests/test_fx.py::TestSeparationFromMntRates`).

**Still awaiting the owner's confirmation:**

- *"4 times a day".* Applied to the international source only. The
  banks are unchanged (every 15 min during 08:00-20:00 Ulaanbaatar,
  hourly otherwise); their freshness matters to a converter and they
  are not metered. `CRAWL_ACTIVE_INTERVAL_MINUTES` /
  `CRAWL_OFFPEAK_INTERVAL_MINUTES` change that if intended.

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
| `app/crawlers/frankfurter.py` | International USD-based foreign-exchange table (not a bank). One request per fetch; own call budget. Not in `HTTP_CRAWLERS`, so `crawlers/__init__.py` stays identical to upstream. |
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
| Frankfurter (`daily`, HTTP side) | `INTL_CRAWLS_PER_DAY` = 4: 00:00, 06:00, 12:00, 18:00, one request each | same (4 requests/day) |

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

### Why Frankfurter is a USD table, fetched 4 times a day

Everything below was read off the live API on 2026-09-29, not assumed.

**History of this decision (kept because it was reversed).** The first
build fetched `base=X&quotes=MNT` once per currency (162 requests a
crawl, MNT per unit, on `/v1/rates`). The owner's conversion rules
then made that wrong twice over: MNT rates come from Mongolian
sources, and foreign↔foreign must not involve MNT at all. It was
replaced by a single `GET /v2/rates?base=USD`. Requests per day fell
from ~324 to 4.

- **`/v1` cannot be used.** ECB-only, 30 currencies, and answers
  `{"message":"not found"}` for MNT. `/v2` lists 166 currencies.
  Base URL `https://api.frankfurter.dev/v2`.
- **One response is the whole table.** 166 rows, each
  `{date, base:"USD", quote, rate}` - units of `quote` per 1 USD.
  160 are published (see exclusions).
- **Why a table and not per-pair requests.** Frankfurter rounds every
  pair it serves to 5 decimal places, so a small pair loses digits.
  Measured against the ratio of two table entries:

  | Pair | Direct endpoint | Via table | Direct is off by |
  | --- | --- | --- | --- |
  | KZT→USD | 0.00227 | 0.0022664 | **0.157%** |
  | IDR→EUR | 0.000049 | 0.0000488703 | **0.265%** |
  | JPY→USD | 0.00636 | 0.0063593 | 0.011% |
  | KZT→EUR | 0.00199 | 0.0019904 | 0.020% |
  | USD→JPY | 157.25 | 157.25 | 0 |
  | EUR→KZT | 502.42 | 502.414 | 0.001% |

  The direct pair is never better; where it differs, it is the
  rounded one. Together with the size of an all-pairs table, this is
  why the owner confirmed the USD pivot (see "Conversion: what the
  backend does and does not do").
- **Reference, not a market rate.** One blended mid figure per
  currency; no buy/sell, no channel. Modelled as the `usd_table`
  channel (`app/sources/models.py`), a *different meaning of `rate`*
  from every other quote (units per USD, not MNT per unit). It is
  therefore served on `/v1/fx` and **never** on `/v1/rates`, so a client
  cannot read 441.22 KZT-per-USD as 441.22 MNT
  (`tests/test_fx.py::TestSeparationFromMntRates`).
- **Blended from central banks.** `expand=providers` shows the
  contributors; USD/MNT alone uses BDI, BOM, CBKKW, CBR, CBU, NBK, NBKR
  and NBP, of which BDI, BOM and CBR carry the Bank of Mongolia's own
  figure (3595.17) and NBP's is six days old. The blend moves through
  the day as providers publish (EUR/MNT read 4093.69, then 4093.55, an
  hour apart), which is why fetching more often than 4 times a day buys
  little.
- **Excluded:** MNT (policy), USD itself, and XAU/XAG/XPD/XPT (a dollar
  buys ~0.0002 troy ounces, which 5 decimals cannot hold; the banks
  already publish metals). 160 currencies published, KZT included.
- **Failure policy.** A row priced against another base fails the crawl
  (it would publish the wrong number under every code). HTTP 429 raises
  `FrankfurterRateLimited`. A non-list body or 5xx fails the crawl and
  the last good snapshot stays served. A pair more than 3 days behind
  the newest is published but reported in the crawl warnings.
- **Call budget.** Frankfurter documents no quota ("no monthly or daily
  caps", only abuse rate-limiting), so the ceiling is self-imposed:
  `INTL_CRAWLS_PER_DAY=4` (must divide 24) and `INTL_DAILY_CALL_LIMIT=4`.
  `DailyCallBudget` refuses the request that would cross it - including
  a manual admin crawl - and `build_scheduler` raises `ValueError` at
  startup if the schedule plans more than the ceiling
  (`INTL_CRAWLS_PER_DAY=24` is rejected). The counter is in-process and
  resets on restart, matching the single-process assumption.
- **Freshness.** Its stated date is the newest across pairs.
  `published_stale_hours=96` (banks: 36) because providers do not
  publish at weekends. Stale after 3 missed fetches (18 h).
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
sought by the owner (Viv Data was assessed and rejected, below) -
anything added must pass the same test: does its licence allow
republishing through an API?

### Viv Data "Currency Converter API" (API.market): do not use

Assessed 2026-09-29 from its own listing (`api.market/store/viv-data/
currency-converter`, read via the page's embedded product record) and
API.market's Terms of Service. **Not legal advice**; the conclusion is a
reading of their published text.

- **It is a resale of ExchangeRate-API's open data.** The listing's own
  FAQ: "Exchange rates are sourced from open.er-api.com" - the endpoint
  rejected above. A wrapper cannot grant rights its upstream withholds,
  and ExchangeRate-API's Terms forbid re-distribution and use in any
  service offering programmatic access to rates. Using it would be the
  same breach by a longer route.
- **API.market's own Terms** (B2B only; "Sell, resell, rent, lease, or
  otherwise commercially exploit the Services without our prior written
  consent") point the same way for a service that republishes the data,
  and defer everything else to each seller's terms - the seller has
  published none.
- **The listing contradicts itself.** Text says Free = 1,000 requests a
  month and Pro = $19.99; the plan configuration says Free = 100 calls
  (HARD limit) and Pro = $9.99 for 10,000. Sample responses are dated
  2023 and show MNT at 3445. No update cadence beyond "cached for 1
  hour".
- **Track record:** published 2026-07-14 by an individual account;
  analytics on the page: 3 total API calls, 1 subscriber, 0 reviews.
- **Nothing to gain:** it needs a key and a paid plan to reach data we
  can already read from its source, at 100-1,000 calls a month.

**A candidate that looks compatible, not yet built:**
`fawazahmed0/exchange-api` (jsDelivr / Cloudflare Pages, no key)
declares CC0-1.0, "no rate limits", daily updates. A live read showed
340 codes (crypto included), MNT present, 8-decimal precision, dated
2026-09-28. **Caveat: it does not state where its numbers come from**,
so CC0 covers the compilation but not necessarily the upstream data.
Confirm provenance (or accept the risk knowingly) before relying on it.
Any second source must satisfy: its licence allows republishing through
an API.

### fxratesapi.com: source #17, restricted to our app

**Built 2026-09-29** after the owner registered a key. Everything below
the assessment was re-verified against the live *keyed* API.

Assessed 2026-09-29 by the owner's request. Read directly: the Terms &
Conditions and Permitted & Prohibited Uses (both v1.0, 28.11.2022; the
site is client-rendered, so the text was read from its page bundles),
the FAQ, and a live keyless response. **Not legal advice.**

Operator: Saritra GmbH, Vienna (FN502707a). Data: "derived from a wide
range of commercial sources, private banks and national banks",
updated every minute - **their own compilation**, licensed by them,
unlike Viv Data (a resale) or fawazahmed0 (provenance unstated).

What the licence allows (T&C, "Scope of the fxRatesAPI API License"):
"receive, process, and display fxRatesAPI API Data & Services to
individual end-users of your application(s), provided such end users
use [it] strictly for their own personal use [and] You do not permit
Your end users to store, distribute, or otherwise exploit" it; "solely
... for reference by Your end users"; "under no circumstances whatsoever
may You transfer ... outside of your application(s)". The FAQ endorses
our shape: "When you only send requests to the API from your backend
and cache the data on your end you can rest assured that we will
consider this fair use." Attribution: "would be highly appreciated"
(requested, not required).

A consumer converter that shows reference rates fits that. The four
conditions found, and how each is now met:

1. **Only our app may receive it.** `/v1/fx` was an open public API,
   which is "transfer outside of your application". → The source is
   `restricted`: `/v1/fx` includes it only for a request with a valid
   `X-App-Key` (see "App authentication" below), otherwise it is simply
   absent - **except in development mode** (no `APP_API_KEYS`), which
   the owner chose for the pre-production period and which production
   cannot run in (see below). Such responses are `Cache-Control: private` with
   `Vary: X-App-Key`, so no shared cache can hand them to a stranger.
2. **No archive.** Prohibited Uses bars archiving/caching "within
   another web site" and redistribution "in any manner whatsoever". →
   `/v1/rates/fxratesapi/history` is a 404 for everyone, the app
   included. Snapshots are still stored internally (change detection
   needs the last one); only exposure is barred.
3. **Registered key, not the public plan.** FAQ: public-plan limits
   "are enforced over all users so we do not recommend using the public
   plan for production use". → `FXRATESAPI_KEY` is required; without it
   the source is never scheduled or served, never degraded to keyless.
4. **Broad "except as permitted in writing" clauses** in Prohibited Uses
   (e.g. embedding data "into any ... application software") sit
   awkwardly next to the T&C grant. → **Still open: the owner should
   ask support@fxratesapi.com to confirm this exact use in writing
   before launch.**

**Found by reading keyed responses (not in their docs):**

- **A wrong key does not fail.** It returns 200 with data from the
  shared public plan (`x-ratelimit-limit: 61`); the registered plan
  answers `-1` / `unlimited`. The crawler checks that header and puts
  "key not honoured" in the crawl warnings rather than trusting the
  status code.
- **The table is not all fiat.** 180 codes include 11 crypto assets
  (one, `OP`, not even three letters), 4 metals and 9 withdrawn ISO
  codes (BYR, CUC, HRK, LTL, LVL, MRO, STD, VEF, ZMK). For MRO, STD and
  VEF the successor code is absent from the table, so which currency
  the number belongs to cannot be told - they are dropped (invariant
  3). MNT and USD are dropped too. **154 currencies published.**
- **It states the minute of publication** (`date`), so `published_at`
  is exact (the adapter now takes a crawler's `published_at` when set).
  The payload hash excludes it, so weekend repeats of the same rates do
  not open new snapshots.
- Verified live end to end: 1 request (1 of 4 budget), 154 quotes,
  second crawl unchanged; `/v1/fx` without key → Frankfurter only
  (`public`), wrong key → same, app key → both (`private`); history
  404 with and without key.

Payload (live, keyless, `GET https://api.fxratesapi.com/latest?base=USD`):
`{success, terms, privacy, timestamp, date, base:"USD", rates:{...}}`,
180 rates including MNT, KZT, CNY, crypto and metals, **10 decimal
places** (KZT 439.4400667645) versus Frankfurter's 5. Headers:
`x-ratelimit-limit: 61`. One request = the whole USD table, so it fits
the pivot design and the 4-a-day ceiling unchanged. Note its data is
intraday market-derived, Frankfurter's central-bank reference: the two
will differ slightly (KZT 439.44 vs 441.22 the same hour), which is
expected, not a bug.

Housekeeping signal: its docs "Request Pricing" page is an unedited
template from a PDF-conversion product ("create more PDFs"). Low effort
on docs; weigh accordingly.

**Operating it - who does what:**

1. *Owner, done:* free account, key stored as the secret environment
   variable `FXRATESAPI_KEY`. The backend sends it only as
   `Authorization: Bearer`, never in a URL, so it cannot leak into
   request logs. Set it on the production host too.
2. *Owner, done on the server (2026-09-30):* `APP_API_KEYS` and
   `REQUIRE_APP_KEY=true` are set on the production service, which
   runs in enforced mode (verified: no key → fxratesapi withheld).
   *Still to do:* build the same value into the iOS app.
3. *Owner, recommended before launch:* email support@fxratesapi.com
   describing the use - rates fetched 4×/day by our backend, cached,
   shown only inside our iOS app for personal reference, no
   redistribution - and keep the written reply.

### App authentication (`X-App-Key`) - what it is and is not

The first endpoint with licence-restricted data needed a way to tell
"our app" from "anyone on the internet". Built as the smallest thing
that meets the licence without breaking anything that exists:

- **Opt-in, per source.** A source is `restricted` in the registry.
  Unrestricted data (the 15 banks, Frankfurter) stays public exactly as
  before, so an app build without the key keeps working. A missing or
  wrong key is not an error; restricted sources are just absent.
- **Development mode (owner's decision, 2026-09-29).** With no
  `APP_API_KEYS` configured, restricted sources are served to *every*
  request, so the feed is usable before the app ships a key. This is
  licence-safe only while the server is not publicly reachable, so:
  - the server logs a `DEVELOPMENT MODE` warning at every startup;
  - `REQUIRE_APP_KEY=true` makes it **refuse to start** with no keys,
    and `render.yaml` (the production blueprint) sets it - so a Render
    deploy cannot run open by accident
    (`tests/test_fx.py::test_render_blueprint_enforces_the_key`);
  - responses with restricted data stay `Cache-Control: private`, and
    history stays 404, in every mode.
  This replaced the first build's "no keys → served to nobody", which
  blocked using the feed during development.
- **Keys are compared in constant time** (`secrets.compare_digest`).
- **Rotatable.** `APP_API_KEYS` is a list: add the new key, ship an app
  version that sends it, remove the old key once old versions are gone.

**Honest limit: this is a gate, not proof.** A key compiled into an iOS
app can be extracted from the binary by a determined person. It stops
the endpoint being open to anyone (the licence problem) and makes
casual reuse impossible, but it does not *prove* a request came from a
genuine install. The stronger step, if fxRatesAPI or growth ever calls
for it, is Apple **App Attest** (per-install keys verified server-side)
- more work on both sides, deliberately not done now.

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

- [x] **Frankfurter** as source #16 (`international_aggregator`).
      *Superseded the same evening*: first built as 161 MNT-per-unit
      quotes on `/v1/rates`; now a USD table on `/v1/fx` (below).
- [x] **Logos** for 15 of 16 sources (`logo_url`), with provenance
      manifest and publisher verification. Naiman Sharga: none, see §5.
- [x] **OpenAPI** snapshot + drift test; remaining v1 endpoints typed.
- [x] Rejected ExchangeRate-API (licence) - see §5.
- [x] 314 tests; isort/black/ruff clean.

**2026-09-29 (evening) - conversion policy, USD table, 4 fetches a day.**

- [x] Owner's **conversion policy** recorded as a binding requirement
      (section "Conversion policy") with three interpretations flagged
      for confirmation. **USD pivot confirmed by the owner the same
      evening**; two remain open.
- [x] Frankfurter reworked from 161 MNT-per-unit quotes to a **USD
      table on `GET /v1/fx`** (160 currencies, one request).
      `/v1/rates` is back to the 15 Mongolian sources.
- [x] International fetch limited to **4 a day** (`INTL_CRAWLS_PER_DAY`),
      4-request daily ceiling, startup guard. Verified live: one
      request, hash stable on the second crawl, ETag/304 on `/v1/fx`.
- [x] Viv Data / API.market assessed and rejected (resale of the
      ExchangeRate-API data).
- [x] 329 tests; isort/black/ruff clean.

**2026-09-29 (night) - conversion logic handed to the app.**

- [x] Owner decided the fallback between sources is **app-only**. The
      tier rules were removed from this document and live only in
      `docs/mobile-integration-prompt.md` rule 11; this document keeps
      only what constrains the backend (USD pivot, two separate feeds).
      No code changed - the backend never implemented the tiers.
- [x] fxratesapi.com onboarding steps recorded (§5).

**2026-09-29 (late night) - fxRatesAPI and app authentication.**

- [x] **fxRatesAPI as source #17** (`usd_table`, 154 currencies, 10
      decimals, exact publication minute), 4 fetches/day with its own
      call budget, disabled without `FXRATESAPI_KEY`. Verified live
      with the owner's key.
- [x] **`X-App-Key` app authentication**, per-source `restricted` flag,
      fail-closed, rotatable. fxRatesAPI served only with the key,
      `private` caching, no history (§5).
- [x] Scheduler checks each international source's budget separately
      and skips any whose key is unset.
- [x] Test suite made independent of real secrets in the environment
      (autouse fixture), and a `config` reload hazard in one test fixed.
- [x] 364 tests; isort/black/ruff clean.

**Later the same night - development mode.**

- [x] Owner asked to use both foreign sources without setting up
      `APP_API_KEYS` until production. No keys → restricted sources
      served to all, with a startup warning; `REQUIRE_APP_KEY=true`
      (set in `render.yaml`) refuses to start open. Verified live:
      plain `/v1/fx` returns both sources; prod-style start with no
      keys fails with a clear error.
- [x] 370 tests; isort/black/ruff clean.

**2026-09-30 - production deployment (§8).**

- [x] Live on Render (free, Singapore) at
      `https://tugrikrate-backend-service.onrender.com`, database on Neon
      (free, AWS Singapore). Auto-deploys from `main` after CI passes.
- [x] Verified live: health 200; Neon tables created, 17 sources
      registered; `APP_API_KEYS` set with `REQUIRE_APP_KEY=true`
      (production mode, no development-mode warning); `/v1/fx` without
      key → Frankfurter only, with key → both, `private`; fxRatesAPI
      history 404; logos served over HTTPS; self-ping healthy.
- [x] **First live crawl (10:16 UTC): all 10 HTTP sources returned
      rates, 389 quotes** - including Khan Bank, which blocks the
      development sandbox's datacenter IP but not Render's. Naiman
      Sharga reported `stale`, correctly (its own publishing gap).
- [x] Docs, `render.yaml` and the mobile brief updated to the deployed
      reality.
- [x] **CI fixed.** The Docker Test job (runs on `main` only, so the PR
      passed) had failed on every push since the merge:
      `.dockerignore` excluded `docs/`, so `tests/test_openapi.py`
      could not read `docs/openapi.json` inside the image. Because
      Render deploys only after CI passes, those pushes were never
      auto-deployed; the live service ran the manually deployed
      `9f24376`. Fixed by re-including only `docs/openapi.json`;
      verified with Docker's own context rules and a full test run on
      exactly that context.

**2026-09-30, 16:00 UTC - first browser and FX crawls on Render.**

- [x] **Chromium fits in 512 MB so far**: 4 of the 5 Playwright banks
      returned rates (Bogd 30, CK 38, NIB 28, TransBank 32 quotes);
      no OOM, no restart, no failed-instance event. NIB's certificate
      problem was the sandbox's, not the bank's.
- [x] **Both FX sources live**: Frankfurter 160 and fxRatesAPI 154
      currencies; no "key not honoured" warning; `/v1/fx` without the
      app key still returns Frankfurter only.
- [x] 12 ok / 2 stale / 1 failing, 517 quotes. Stale are Naiman Sharga
      (last published 5 days ago) and CK (2 days) - both the sources'
      own dates.
- [ ] **TDBM returns 0 at the 00:00 Ulaanbaatar run**: at that minute
      its page lists the currencies with no rate cells - the new day
      is not published yet. The 5 browser banks' only overnight slot
      is 00:00, so TDBM fails there nightly and keeps its last good
      snapshot; its first successful live run should be 08:00
      Ulaanbaatar. Not yet seen succeed on Render.
- [x] **Bug found: intermittent 500s from idle database connections.**
      Neon suspends compute after ~5 minutes idle and closes pooled
      connections (`AdminShutdown: terminating connection due to
      administrator command`); the next request got the dead
      connection. Fixed with `pool_pre_ping=True`
      (`tests/test_database.py` simulates a killed connection; the
      same simulation without pre-ping fails).

**Not done / known gaps**

- [ ] **The iOS app is not connected yet.** No client exists.
- [ ] **Browser banks and foreign sources not yet seen live.** The 5
      Playwright banks and both FX sources first run on Render at 00:00
      Ulaanbaatar (16:00 UTC) on 2026-09-30 - the first time headless
      Chromium runs in the free instance's 512 MB, where upstream hit
      OOM kills. If it fails: `CRAWL_GROUP=fast` (drop those 5) or a
      2 GB instance (§8).
- [ ] **The iOS app must ship the `APP_API_KEYS` value** as its
      `X-App-Key`; production withholds fxRatesAPI without it.
- [ ] **Neon free-plan usage is estimated, not measured** (100 CU-hours
      and 0.5 GB a month). Check the Neon dashboard after a week.
- [ ] **fxRatesAPI written confirmation** of the use, recommended
      before launch (§5, condition 4).
- [ ] **App authentication is a shared secret, not App Attest** (§5).
- [ ] **Conversion is not implemented anywhere yet** - it is the iOS
      app's job (mobile brief, rule 11). Backend-side, one question is
      open: whether "4 a day" also covers the banks.
- [ ] **Frankfurter licence not audited per provider** (§5).
- [ ] **NIB's crawler is unverified end to end.** Its certificate chain
      did not verify from the development sandbox; it is one of the
      browser banks first run on Render at 16:00 UTC. (Khan Bank, the
      other sandbox failure, is confirmed working from Render.)
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

## 8. Production deployment

**Where.** One Render **Web Service**, `tugrikrate-backend-service`
(`srv-daudq6ugekts73e2ke90`), free plan, region Singapore, Docker
runtime, at `https://tugrikrate-backend-service.onrender.com`. Database:
Neon free Postgres, project `icy-shape-78149675`, branch `production`,
AWS Singapore. Chosen 2026-09-30 after comparing free tiers (Render,
Oracle Always Free, Koyeb, Fly.io, Railway, Cloud Run): Render needs no
server administration and was already scripted; Oracle has more RAM
but must be run by hand; the others either sleep, have no free plan, or
cannot host an in-process scheduler.

**How a change ships.** Push to `main` → GitHub Actions CI (isort,
black, ruff, pytest, Docker build) → Render deploys **only after CI
passes** (`autoDeployTrigger: checksPass`). A deploy restarts the
process, and with it the in-process scheduler.

**The service was created by hand, not from the Blueprint.** A Blueprint
attempt made a second service with no database; running both would
have doubled every crawl (see "In-process state" in CLAUDE.md), so it
was deleted. `render.yaml` now mirrors the live service's settings so
it stays an accurate record, but Render does not read it for the
existing service - settings are changed in the dashboard (or the Render
API), and `render.yaml` must be kept in step by hand.

**Environment variables on the service** (secrets set in the dashboard,
never in the repo): `DATABASE_URL` (Neon *direct* string - not the
`-pooler` host, which is unneeded for one process and risky with
psycopg's prepared statements), `APP_API_KEYS`, `REQUIRE_APP_KEY=true`,
`FXRATESAPI_KEY`, `ADMIN_API_KEY`, `PUBLIC_BASE_URL` and
`SELF_PING_URL` (both must include `https://` - without it the first
deploy produced logo URLs starting with `-` and a failing self-ping),
and the 512 MB tuning `MAX_WORKERS=4`, `PLAYWRIGHT_MAX_WORKERS=1`,
`CRAWL_PLAYWRIGHT_MULTIPLIER=8`, `TRUST_PROXY_HEADERS=true`.

**Free-plan limits that shape it.**
- The instance sleeps after 15 minutes without inbound traffic, which
  would stop the scheduler; `SELF_PING_URL` keeps it awake. One
  always-on service uses ~744 of the 750 free hours a month, so there
  is room for exactly one such service.
- 512 MB RAM / 0.1 CPU: see the first known gap in §6. Fallbacks, in
  order: `CRAWL_GROUP=fast`; a 2 GB instance ($25/month on Render's
  Standard plan - its $7 Starter plan is still 512 MB).
- Neon free: 0.5 GB storage, 100 CU-hours a month, compute suspends
  after 5 minutes idle and each crawl wakes it. Suspension closes
  every pooled connection, which is why the engine uses
  `pool_pre_ping=True` (without it, the first request after an idle
  spell returned a 500).

**Checking it.** `GET /api/health` (does not touch the database);
`GET /v1/rates` (reads the database: 200 means connected); with the
admin key, `POST /api/admin/crawl/<source>` crawls one source now.
Render's API (`https://api.render.com/v1`, `Authorization: Bearer`)
gives deploys, logs and env vars; from a sandbox that blocks port 5432,
Neon is reachable through `POST https://<host>/sql` with the
`Neon-Connection-String` header.
