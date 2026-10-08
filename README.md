# TugrikRate Rates

Backend service for **TugrikRate**, an iOS currency converter for the
Mongolian tögrög (MNT). It collects exchange rates from 15 Mongolian
banks and financial institutions, stores them as immutable snapshots,
and serves them over one stable JSON contract.

The iOS app talks only to this service. It never contacts a bank
directly.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Credit

This project began as a fork of
**[btseee/mongolian-bank-exchange-rate](https://github.com/btseee/mongolian-bank-exchange-rate)**
by **[Battseren Badral](https://github.com/btseee)**, MIT licensed. All
15 bank crawlers originate from that project and it remains the upstream
for crawler fixes. The original MIT license is retained in
[LICENSE.md](LICENSE.md). Thank you.

Upstream is configured as the `upstream` remote:

```bash
git fetch upstream
git log --oneline HEAD..upstream/main -- app/crawlers/   # crawler fixes
git cherry-pick <sha>
```

`app/crawlers/` is deliberately kept close to upstream so those fixes
merge cleanly. Everything a crawler *claims* — which channels it
publishes, which side is which, what unit a rate is quoted in — lives in
`app/sources/registry.py` instead, which upstream never touches.

## The contract

`GET /v1/rates`

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-29T03:15:00Z",
  "sources": [
    {
      "id": "khanbank",
      "name": "Khan Bank",
      "type": "commercial_bank",
      "status": "ok",
      "fetched_at": "2026-09-29T03:14:52Z",
      "published_at": null,
      "last_checked_at": "2026-09-29T03:14:52Z",
      "quotes": [
        {
          "currency": "USD",
          "channel": "noncash",
          "side": "sell",
          "rate": "3596.00",
          "unit_basis": "1",
          "verified": true
        }
      ]
    }
  ]
}
```

Guarantees the app can rely on:

| Rule | Why |
| --- | --- |
| `rate` and `unit_basis` are **strings** | A JSON number is a double in every client. `"3450.50"` stays exactly that. |
| A missing rate is **absent** | There is no null rate and no `0` standing in for "not published". |
| `channel`/`side` are what the source really publishes | Nothing is copied between channels or inferred. |
| A stale or failing source still returns its last good quotes | Losing rates because one bank had a bad afternoon is worse than showing them flagged. |
| `published_at` is null unless the source states it | Never back-filled from fetch time. |
| Timestamps are UTC, `Z`-suffixed | |

`side` is always from the **source's** perspective: `buy` is what it
pays you, `sell` is what it charges you.

`channel` is one of:

- `cash` / `noncash` — the source labels them explicitly.
- `reference` — an official rate with no spread (Bank of Mongolia only;
  `side` is also `reference`).
- `unspecified` — the source publishes one buy/sell pair and never says
  which channel it applies to. **Not a guess.** Three sources are in
  this category: M Bank, SendMN, Naiman Sharga.

`status` is one of:

- `ok` — crawled recently, publishing recent data.
- `stale` — either we have not fetched it successfully for several
  intervals, or the source itself is serving rates it published days
  ago. Quotes are still returned.
- `failing` — repeated crawl failures, or no data ever collected.

`verified: false` means the *quoting unit* could not be confirmed —
treat the rate as indicative. Currently this applies only to precious
metals (`XAU`/`XAG` and the non-ISO spellings some banks use for them,
which are quoted per gram by some sources and per troy ounce by others)
and to `KPW`.

### Other endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/rates?currency=USD,EUR&source=khanbank` | Filter the feed |
| `GET /v1/sources` | Registry, including the evidence behind each channel mapping |
| `GET /v1/rates/{source_id}/history?limit=50` | Distinct snapshots, newest first |
| `GET /api/health` | Health check |
| `GET /api/info` | Service info |
| `POST /api/admin/crawl` | Trigger a crawl (`X-Admin-Key`) |
| `POST /api/admin/crawl/{source_id}` | Trigger one source (`X-Admin-Key`) |
| `GET /api/admin/status` | Last/current job state (`X-Admin-Key`) |

`/v1/rates` supports `If-None-Match`, so an app polling on an interval
gets a bare `304` when nothing has moved.

Swagger UI is at `/`.

## How collection works

**Cadence** (all Asia/Ulaanbaatar):

| Sources | 08:00–20:00 | Otherwise |
| --- | --- | --- |
| 10 JSON-API sources | every 30 min | hourly |
| 5 rendered-page sources (Playwright) | every 2 h | every 4 h |

Every trigger carries random jitter (`CRAWL_JITTER_SECONDS`, default
±90s) so no bank is hit on an exact interval boundary. The browser
sources are throttled by `CRAWL_PLAYWRIGHT_MULTIPLIER` because each
crawl costs a headless Chromium.

**Snapshots.** A `rate_snapshots` row is written only when the payload
hash changes. An unchanged crawl bumps `last_checked_at` on the existing
row. The hash is taken over a *canonical* payload — JSON re-serialised
with sorted keys and volatile fields removed — because rendered pages
carry build ids and analytics state that change on every load, and some
JSON APIs embed data that moves without the rates moving.

**Isolation.** Every source crawls in its own try/except with its own
database session. One bank failing cannot affect another's result, and
a failure never touches the last good snapshot.

## Sources

| Source | id | Channels published | Transport |
| --- | --- | --- | --- |
| Khan Bank | `khanbank` | cash, noncash | JSON |
| Golomt Bank | `golomtbank` | cash, noncash | JSON |
| XacBank | `xacbank` | cash, noncash | JSON |
| Arig Bank | `arigbank` | cash, noncash | JSON |
| State Bank | `statebank` | cash, noncash | JSON |
| Bank of Mongolia | `mongolbank` | reference | JSON |
| Capitron Bank | `capitronbank` | cash, noncash | JSON |
| Naiman Sharga | `naimansharga` | unspecified | JSON |
| SendMN | `sendmn` | unspecified | JSON |
| M Bank | `mbank` | unspecified | JSON |
| Trade and Development Bank | `tdbm` | cash, noncash | Playwright |
| Bogd Bank | `bogdbank` | cash, noncash | Playwright |
| Chinggis Khaan Bank | `ckbank` | cash, noncash | Playwright |
| National Investment Bank | `nibank` | cash, noncash | Playwright |
| Trans Bank | `transbank` | cash, noncash | Playwright |

`GET /v1/sources` returns the evidence recorded for each mapping.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python -m scripts.migrate_v1     # create the schema
python main.py                   # one full crawl, then exit
python -m uvicorn app.api.api:app --reload
```

Docker:

```bash
docker compose up --build
```

The scheduler runs inside the API process, so a single process is
enough to run everything. See below for splitting the Playwright
sources into their own worker.

### Splitting the browser sources into their own process

Each headless Chromium is the largest memory consumer in the service,
and an OOM kill in a combined process takes the API down with it —
upstream hit exactly that repeatedly on a 512MB instance. `CRAWL_GROUP`
splits collection across two processes:

| Process | `CRAWL_GROUP` | Owns | Command |
| --- | --- | --- | --- |
| Web | `fast` | 10 JSON sources + serves `/v1/rates` | `uvicorn app.api.api:app` |
| Worker | `slow` | 5 Playwright sources | `python -m scripts.worker` |

`docker compose up` runs this split by default. The two groups own
disjoint sources and share only the database, so they need **no**
coordination — neither can ever write the other's rows, and each
`/api/admin/crawl` is scoped to its own process's group. Both must
point at the same `DATABASE_URL`, and once there is more than one
process that has to be Postgres rather than SQLite.

To keep everything in one process, set `CRAWL_GROUP=all` (the default
when unset) and don't run the worker.

On Render, a worker is a paid process type; `render.yaml` ships the
single-process configuration with the worker block commented out.

### Production (Render + Neon)

Live at **https://tugrikrate-backend-service.onrender.com** (Swagger UI
at `/`). One Render Web Service on the free plan in Singapore, with the
database on Neon's free Postgres (AWS Singapore). Every push to `main`
deploys automatically once CI passes. Full record, limits and
rationale: ARCHITECTURE.md §8.

To recreate it from scratch:

1. **Neon** - create a project in AWS Singapore (Postgres only), open
   *Connect*, turn **connection pooling off**, and copy the direct
   connection string. No schema step: the app creates its tables on
   start.
2. **Render** - *New > Web Service* from this repo, branch `main`,
   runtime **Docker**, region **Singapore**, plan **Free**, health check
   `/api/health`, auto-deploy **after CI checks pass**. (Or *New >
   Blueprint*, which reads `render.yaml` - but never run both: two
   copies of the in-process scheduler double every crawl.)
3. **Environment** - `DATABASE_URL`, `APP_API_KEYS`,
   `REQUIRE_APP_KEY=true`, `FXRATESAPI_KEY`, `ADMIN_API_KEY`,
   `MAX_WORKERS=4`, `PLAYWRIGHT_MAX_WORKERS=1`,
   `CRAWL_PLAYWRIGHT_MULTIPLIER=8`, `TRUST_PROXY_HEADERS=true`; then,
   once the URL exists, `PUBLIC_BASE_URL=https://<service>.onrender.com`
   and `SELF_PING_URL=https://<service>.onrender.com/api/health` (both
   **with** `https://`).
4. **Check** - `/api/health`, then `/v1/rates` (200 means the database
   is connected); sources fill in at the next scheduled crawl.

### Other deployment notes

A 30-minute cadence is below what an external HTTP trigger (GitHub
Actions cron, for example) can reliably hold — which is why the
scheduler moved in-process. Set `SCHEDULER_ENABLED=false` and drive
`POST /api/admin/crawl` yourself if you would rather run collection
somewhere else.

Free tiers have no persistent disk, so the default SQLite database is
wiped on restart. Set `DATABASE_URL` to a real Postgres connection
string for anything you want to keep.

## Configuration

Everything has a working default; see `app/config.py` for the full list.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | SQLite | Postgres connection string in production |
| `ADMIN_API_KEY` | (empty) | Secures `/api/admin/*`. Empty means 503, never open |
| `SCHEDULER_ENABLED` | `true` | Run the in-process scheduler |
| `CRAWL_TIMEZONE` | `Asia/Ulaanbaatar` | Cadence is local, not UTC |
| `CRAWL_ACTIVE_START_HOUR` / `_END_HOUR` | `8` / `20` | Fast-cadence window |
| `CRAWL_ACTIVE_INTERVAL_MINUTES` | `30` | Interval inside that window |
| `CRAWL_OFFPEAK_INTERVAL_MINUTES` | `60` | Interval outside it |
| `CRAWL_JITTER_SECONDS` | `90` | Random spread on every trigger |
| `CRAWL_GROUP` | `all` | `all`, `fast` or `slow` — which sources this process collects |
| `CRAWL_PLAYWRIGHT_MULTIPLIER` | `4` | How much less often browser sources run |
| `SOURCE_LOOKBACK_DAYS` | `7` | How far back a source may look for its last publication |
| `STALE_AFTER_INTERVALS` | `3` | Missed intervals before `stale` |
| `FAILING_AFTER_ATTEMPTS` | `3` | Consecutive failures before `failing` |
| `PUBLISHED_STALE_HOURS` | `36` | Publication age before `stale` |
| `SNAPSHOT_RETENTION_DAYS` | `0` | `0` keeps everything |
| `MAX_WORKERS` / `PLAYWRIGHT_MAX_WORKERS` | `8` / `3` | Crawl concurrency |
| `CRAWL_BATCH_DEADLINE_SECONDS` | `600` | How long a run waits for its sources; overdue ones keep running and still save |
| `SELF_PING_URL` | (empty) | Keeps a sleeping free-tier instance awake |
| `CORS_ORIGINS`, `RATE_LIMIT_*` | — | Public API safeguards |
| `INTL_CRAWLS_PER_DAY` / `INTL_DAILY_CALL_LIMIT` | `4` / `4` | International fetches per day, and the hard per-source ceiling |
| `FXRATESAPI_KEY` | (empty) | fxRatesAPI key (secret). Empty disables that source |
| `APP_API_KEYS` | (empty) | Comma list the iOS app sends as `X-App-Key`. **Empty = development mode**: app-only sources (fxRatesAPI) go to any caller - never on a public server |
| `REQUIRE_APP_KEY` | `false` | `true` refuses to start while `APP_API_KEYS` is empty. Set in `render.yaml` for production |
| `PUBLIC_BASE_URL` | (request origin) | Absolute base for `logo_url`; set it in production |

Rate limiting and the admin job lock are in-process, so they are only
correct with a single Uvicorn process (no `--workers`).

## Development

```bash
isort app tests scripts main.py --check-only && black app tests scripts main.py --check
ruff check app tests scripts main.py
pytest
```

`scripts/probe_units.py` is a standalone diagnostic that re-derives each
source's unit basis from live data, by anchoring every currency against
that source's own USD rate. Run it if you suspect a bank has changed how
it quotes:

```bash
python -m scripts.probe_units             # all sources
python -m scripts.probe_units khanbank    # one source
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — why the service exists, how it
  is built, the evidence behind each decision, and what is done so far.
- [CLAUDE.md](CLAUDE.md) — working rules and gotchas for anyone (human
  or agent) changing this repo.
- [docs/mobile-integration-prompt.md](docs/mobile-integration-prompt.md)
  — a self-contained brief for building the iOS client against the v1
  contract. Keep it in sync when the contract changes.

## License

MIT — see [LICENSE.md](LICENSE.md). Original work © Battseren Badral.
