# Upstream and Render audit — 2026-10-31

## Resume here

**Verification follow-up in progress.** Maintenance commit `e735b79` is
pushed and live; GitHub CI, including Docker tests, passed. The live public
API passed 46 checks and Naiman Sharga recovered. Verification found an
OpenAPI app/admin security-scheme collision, now fixed locally with a
regression check. Finish the follow-up deployment and authenticated checks,
then record the dashboard configuration/billing evidence below. The owner
authorized push, deployment, live verification and documentation updates.

User request: read the project documentation, analyze recent upstream changes
and adopt needed fixes, explain deployment configuration, and assess the Render
750-hour allowance with a freshness-preserving schedule recommendation.
Analyze first, then record the implementation plan and mark completion here.

Starting revision: `09b22ca` on `main`; working tree initially clean.
Origin: `nmka55/tugrikrate-backend`. Documented upstream:
`btseee/mongolian-bank-exchange-rate` (no local upstream remote configured).
Production is a manually configured Render service; pushing `main` deploys.
Do not confuse local changes to `render.yaml` with live configuration changes.

## Analysis checklist

- [x] Read all project documentation and relevant deployment/runtime code.
- [x] Identify upstream tip, common ancestor, changelog and relevant diffs.
- [x] Classify upstream changes: adopt, already addressed, or deliberately skip.
- [x] Verify Render billing/sleep rules using official documentation.
- [x] Inventory configured jobs and calculate monthly usage and freshness.
- [x] Identify live-account evidence available and unresolved configuration.
- [x] Record a concrete implementation plan after analysis.

## Implementation checklist

- [x] Apply selected fixes, preserving fork invariants and API contract.
- [x] Add regression coverage for behavior changes.
- [x] Update architecture/configuration/deployment documentation.
- [x] Run isort, Black, Ruff, full pytest and applicable build checks (Docker
      unavailable; production-only startup smoke test passed).
- [x] Record final outcomes, deployment steps and remaining external actions.

## Confirmed constraints

- Exact decimal rates; only source-published channels; missing quotes stay absent.
- Preserve deliberate crawler divergences (see `CLAUDE.md`).
- One Uvicorn process and one scheduler per crawl group.
- `REQUIRE_APP_KEY=true` in production; retain Neon pool pre-ping.
- No commits, push or live deployment performed as part of the initial audit.
- User supplied the warning received four days ago: 619 of 750 hours used,
  reset at next calendar month, listing only this service.
- Render connection confirmed one free service / one instance, previews off,
  not suspended; deployed commit `09b22cab88b1893d449bc8af6f67f88c904dd59f`.

## Findings and decisions

Upstream tip: `6cde736a8132468cfd5fc38f07a961cad0668f51`, release v1.1.1
dated 2026-10-26. Merge base: `73b1db8544edbc2ec404e4fa18f9099185f90a62`.
Fetched into local Git objects (FETCH_HEAD); no merge or cherry-pick done.

Sources:
- [Upstream changelog](https://github.com/btseee/mongolian-bank-exchange-rate/blob/6cde736a8132468cfd5fc38f07a961cad0668f51/CHANGELOG.md)
- [Upstream comparison](https://github.com/btseee/mongolian-bank-exchange-rate/compare/73b1db8...6cde736)
- [Render free service rules](https://render.com/docs/free#monthly-usage-limits)
- [Live service](https://dashboard.render.com/web/srv-daudq6ugekts73e2ke90)

| Upstream change | Decision for this fork |
| --- | --- |
| Naiman Sharga new collection/company document (`2c06c36`) | Adopt URL/config; preserve seven-day lookback, exact decimals, publication date, unspecified channel. Old diagnosis "not a crawler bug" is superseded. |
| TransBank homepage/hidden script wait (`0a8dcb3`) | Adopt; preserve existing side correction and snapshot handling. |
| ArigBank per-bank proxy (`ab7930a`) | Adopt optional setting, no proxy required by default. |
| Non-ASCII admin auth (`045ac67`) | Adopt for admin AND fork-only app auth, which has the same compare_digest bug. |
| black/httpx2 updates (`947adc2`) | Adopt 26.10.1 / 2.16.0 respectively; preserve formatter target py313. Upstream reports six Dependabot alerts; not independently claiming a full vulnerability audit. |
| Separate development requirements (`8b755ad`) | Adopt and update CI, Docker test stage, install docs. |
| Health checks and Docker exclusions (`f421365`) | Adopt status checking and local-state exclusions; preserve docs/openapi.json and render.yaml used by tests. |
| TransBank side swap / zero strings | Already handled by this fork's Decimal parser and side mapping. |
| Empty crawl failure/retry | Already records failure and preserves last good data; retain next-slot retry to avoid extra requests and international budget changes. |
| Legacy latest/backfill ordering | Skip: this fork has immutable snapshots and no backfill; publication-date ordering would hide legitimate current stale-source observations. |
| Remove Render, self-ping and httpx | Skip: Render is still our host and httpx powers self-ping. |
| Upstream version metadata | Keep fork version 2.0.0 and contract unchanged. |

## Render usage analysis

The 750 hours are workspace-wide running-instance hours, not the sum of
crawler durations, thread count, CPU time, requests or scheduler jobs.
Confirmed live inventory contains just this free web service (one instance).
The Docker Compose worker is a local/self-hosted setup, not a deployed Render
worker. No scheduled-crawl GitHub workflow remains; weekly CodeQL runs on
GitHub, not Render. Render worker/cron services are paid, not extra free hours.

| Calendar month length | One continuously running instance | Allowance left |
| --- | ---: | ---: |
| 28 days | 672 h | 78 h |
| 29 days | 696 h | 54 h |
| 30 days (November 2026) | 720 h | 30 h |
| 31 days (October 2026) | 744 h | 6 h |

619 hours = 82.53%; 131 hours remained at the email. The warning is expected
late in a month and does not itself predict exhaustion. Current billed hours
are not exposed by the connected tools. Inventory is a present-day snapshot,
not proof that no other service ever ran earlier in the month.

Keep the current cadence (every day, Asia/Ulaanbaatar):

| Group | Slots | Runs per source/day | Source runs/31 days |
| --- | --- | ---: | ---: |
| 10 JSON banks | 09:00–18:00 every 45 min; 00:00, 06:00 | 15 | 4,650 |
| 5 browser banks | 04:00, 09:00, 12:00, 15:00, 18:00, 23:00 | 6 | 930 |
| 2 international sources (if configured) | 00:00, 06:00, 12:00, 18:00 | 4 | 248 |

Total 188 source runs/day (5,828 in 31 days). These are NOT HTTP request
counts: retries, sign-in, lookbacks and browser assets add requests. Each
foreign source normally makes one request per run: 124/month at most under
this schedule. Failed crawls and source publication delays can exceed the
nominal freshness gaps; the API continues serving flagged last-good data.

Deployed code defaults to 30 s: 89,280 requests/31 days. The local update
defaults to 600 s (10 min): 4,464 requests, 95% fewer, under Render's 15-minute idle
window. This saves request overhead, NOT instance hours. Do not simply
disable keep-alive: a sleeping process cannot run or wake its own scheduler.

Read-only Render checks: no server_failed, service_suspended or
instance_count_changed events in the last seven days. Hourly memory samples
over Oct 30–31 were about 214–326 MiB of 512 MiB; these are samples, not a
guarantee about peak memory. Keep MAX_WORKERS=4, PLAYWRIGHT_MAX_WORKERS=1.

## Implementation plan (analysis completed before code edits)

1. Add failing regression cases for company URLs/lookback, homepage-only
   TransBank, proxy scope, and non-ASCII admin/app keys.
2. Port the selected crawler/config/auth changes; preserve API and data rules.
3. Split dev dependencies, update pins, CI and Docker health/context behavior.
4. Set the default keep-alive interval to 600 s and document the live override
   check; leave bank and FX cadence unchanged.
5. Update README, CLAUDE, CONTRIBUTING, CHANGELOG, registry evidence and
   ARCHITECTURE, with an exact Render configuration checklist.
6. Run full lint/test sequence (baseline: **375 passed** on Python 3.13.16),
   isolated live checks if network permits, and Docker validation if available.
7. Review diff and record deployment handoff. Subsequently authorized by the
   owner: push, deploy, verify production, and record the actual results.

## Current validation / limitations

- Baseline: 375 tests passed (4.23 s).
- Final: **381 tests passed (2.56 s)**; isort, Black and Ruff all pass.
  `pip check` reports no broken requirements. The collector concurrency
  regression also passed 10 consecutive independent test runs.
- A clean temporary environment installed only `requirements.txt` and started
  the API with an isolated SQLite database and scheduler disabled. Health,
  rates, FX and OpenAPI returned 200; no Black/isort/Ruff/pytest/httpx2 was
  installed. OpenAPI matches the committed contract exactly.
- YAML manifests parsed successfully; required Docker test inputs remain
  included. A real Docker image build was not run because Docker is absent;
  CI must still run it before deployment. No remote CI run triggered.
- Final diff reviewed; whitespace check passes with `cr-at-eol` (the existing
  CI and CONTRIBUTING files deliberately retain their original CRLF lines).
- Local Python: 3.13.16; CI/Docker target: 3.14.
- Docker executable is not available locally.
- Six regression cases failed before the patches and passed afterwards.
- Updated dependencies installed successfully (Black 26.10.1, httpx2 and
  httpcore2 2.16.0); initial isort/Black/Ruff checks pass.
- First full run after changes found an existing concurrent-test fixture
  race: StaticPool shares one SQLite connection between collector threads,
  allowing another session's rollback/close to undo a reported successful
  write. Only collector tests now use a temporary file database and separate
  connections. Production collection/database behavior is unchanged.
- Initial network probes failed at sandbox DNS; approved network-enabled
  read-only probes then succeeded. No production data was written.
- Live feed: 13 banks ok; Naiman Sharga failing (last checked Oct 3),
  ArigBank failing (last checked Oct 21). TransBank is currently ok even
  though upstream experienced HTTP 500 from its old URL on Oct 26.
- Patched Naiman Sharga: 44 quotes, publication Oct 31 (Oct 30 16:00 UTC),
  channel unspecified. Company metadata matches NSHMT / Найман шарга
  мэдээллийн төв. This fixes the obsolete-collection diagnosis.
- Patched TransBank: actual Playwright crawl succeeds, 10 currency entries,
  publication Oct 31, USD cash buy 3591 / sell 3621.
- ArigBank direct sign-in timed out locally; a working proxy has NOT been
  supplied or tested. Optional proxy support alone does not fix reachability.
- Render logs query returned service-unavailable (503); events and metrics
  were available. Do not claim to know the exact logged ArigBank exception.
- Render tools expose services/deploys/events/metrics/logs, but no read-only
  environment-variable or billing-total endpoint. Never use the update tool
  to infer settings.

## Deployment/configuration handoff

At release start, changes are on `codex/upstream-render-audit`, uncommitted,
and production runs `09b22ca`. The owner authorized deployment of this work.
Commit and fast-forward main (remote main verified unchanged), push, and
let the existing after-CI auto-deploy run. Do not create a second service or
trigger a duplicate deploy. Preserve existing secrets and provider budgets.

Check these settings in the existing Render service's Environment page.
The plugin cannot read them, so existing overrides are unverified:

| Setting | Required action / value |
| --- | --- |
| `NSHARGA_FIRESTORE_BASE_URL` | Remove an old override to inherit the new `currency_rates_new` URL from `app/config.py`; never leave it pointing at `currency_rates`. |
| `NSHARGA_COMPANY_ID` | Unset uses `sdKPfYBtU9anGKBmOSiDcpKSnGB3` (NSHMT). Use this value if setting explicitly; do not select an arbitrary company's rates. |
| `TRANSBANK_URI` | Remove override, or set `https://transbank.mn/en`. |
| `ARIGBANK_PROXY_URL` | If still unreachable from Render, supply a trusted HTTP(S) proxy that reaches `www.arigbank.mn`. Store credentials as secrets, not in Git. No global `HTTPS_PROXY` needed. |
| `ARIGBANK_BEARER_TOKEN` | Optional; the crawler signs in fresh if absent and refreshes expired tokens. A token cannot fix a connection timeout. |
| `SELF_PING_URL` | Keep `https://tugrikrate-backend-service.onrender.com/api/health`. |
| `SELF_PING_INTERVAL_SECONDS` | Remove old override to use 600 after deploy, or set `600`. Saves health requests, not hours. |
| `SCHEDULER_ENABLED` / `CRAWL_GROUP` | `true` / `all`; one Uvicorn process; no separate Render worker. |
| `CRAWL_TIMEZONE` | `Asia/Ulaanbaatar` (UTC+8; one hour behind Seoul). |
| Active hours / JSON intervals | `9`–`18`; `CRAWL_JSON_ACTIVE_MINUTES=45`, `CRAWL_JSON_OFFPEAK_MINUTES=360`. Defaults suffice. |
| Browser intervals | `CRAWL_BROWSER_ACTIVE_MINUTES=180`, `CRAWL_BROWSER_OFFPEAK_MINUTES=300`. Defaults suffice. |
| `INTL_CRAWLS_PER_DAY` / `INTL_DAILY_CALL_LIMIT` | Keep `4` / `4`. |
| `MAX_WORKERS` / `PLAYWRIGHT_MAX_WORKERS` | Keep `4` / `1` for the 512 MiB instance. |
| Obsolete cadence settings | Remove `CRAWL_ACTIVE_INTERVAL_MINUTES`, `CRAWL_OFFPEAK_INTERVAL_MINUTES`, `CRAWL_PLAYWRIGHT_MULTIPLIER` if present; current code ignores them. |
| Existing secrets and safeguards | Preserve Neon `DATABASE_URL`, `ADMIN_API_KEY`, `APP_API_KEYS`, `FXRATESAPI_KEY`, `REQUIRE_APP_KEY=true`, `PUBLIC_BASE_URL`, `TRUST_PROXY_HEADERS=true`. |

No new database tables, migration, frontend change, or private provider key
is needed for the Naiman Sharga/TransBank fixes. Local/CI test setup now uses
`pip install -r requirements-dev.txt`; production uses `requirements.txt`.

- [ ] Commit/review and deploy to the existing service after CI passes.
- [ ] Check/remove old environment overrides above (dashboard required).
- [ ] Supply/test ArigBank proxy if connection still fails after deployment.
- [ ] Verify `/api/health` and `/v1/rates`; Naiman Sharga should recover at
      the next JSON slot (or an authenticated single-source admin crawl).
- [ ] Check current total on Render Billing; current connector lacks totals.

These boxes are the future deployment handoff, not claims of completed work.

## Reproduce local verification

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m isort app tests scripts main.py --check-only
.venv/bin/python -m black app tests scripts main.py --check
.venv/bin/python -m ruff check app tests scripts main.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git -c core.whitespace=cr-at-eol diff --check
```

New files: `requirements-dev.txt` and this audit. No public API fields or
schema version changed. Temporary runtime smoke-test files are outside the
repo at `/private/tmp/tugrikrate-runtime-audit-20261031`.

## Authorized release checklist — October 31

- [x] Commit reviewed code/docs and push to main without rewriting history.
- [x] Confirm GitHub CI including Docker tests succeeds for that commit.
- [x] Confirm that exact commit reaches Render `live`.
- [ ] Verify health, schema, rates, FX restrictions, filters, caching, history,
      logos and admin protection; verify post-deploy crawler results.
- [ ] Record actual outcomes, remaining bank issues and configuration actions
      here and in related architecture/changelog documentation.

Release policy: the same service, plan, database and crawl cadence remain in
use. No new proxy purchase, secret rotation or extra international crawl is
implied by deployment verification. ArigBank connectivity may remain an
external dependency; report its real state instead of claiming all banks work.

## Release evidence — October 31

- Maintenance commit: `e735b7972839714cb91d517363fbebfcd667e1e0`.
- [GitHub CI run 41164395439](https://github.com/nmka55/tugrikrate-backend/actions/runs/41164395439):
  Lint & Test, Docker Test and Publish Docker Image succeeded; Create
  Release skipped as expected for a branch push.
- Render deploy `dep-dbim25dg1s2s73f5r3i0` became live at
  **03:33:47 UTC / 12:33:47 Seoul**. One free Singapore instance, previews
  off, `checksPass` auto-deploy and `/api/health` unchanged.
- Public API probe at 11:40 UTC: **46/46 assertions passed**. Health is
  200, version 2.0.0; live OpenAPI matched the committed schema; 15 MNT
  sources and 17 registry entries; positive decimal-string rates and
  units; correct channels; UTC timestamps; source/currency filters;
  ETags with empty 304 responses; bounded history and invalid-limit 422;
  restricted/unknown history 404; all 16 hosted logos load.
- Public FX contains only Frankfurter (164 rates), never MNT or
  fxRatesAPI. Missing/wrong/non-ASCII app keys withhold restricted data;
  missing/wrong/non-ASCII admin keys return 401. Public FX is cached
  with `Vary: X-App-Key, Origin, Accept-Encoding`. The initial probe
  incorrectly required an exact one-item Vary value; a focused recheck
  confirmed list membership and passed. No server change was needed.
- Bank status at that probe: **12 ok, 2 stale, 1 failing**. Naiman
  Sharga's first post-deploy recovery was 04:01:32 UTC, 44 quotes;
  last checked 10:00:51 UTC. TransBank: 32 quotes, checked 10:02:15 UTC.
  XacBank/CKBank last checked October 31 but publish October 30 rates
  (`2026-10-29T16:00:00Z`, midnight Ulaanbaatar), so stale is accurate.
  ArigBank retains 40 quotes, last success October 21, with continuing
  sign-in connection timeouts. It needs a trusted reachable proxy.
- Scheduled fxRatesAPI crawls succeeded at 04:00 and 10:00 UTC (154
  quotes). No manual international crawl was triggered. The scheduler
  logged its four-calls-per-day budget at startup.
- No post-deploy failure/restart/suspension events were returned.
  Recent sampled memory was 214–236 MiB out of 512 MiB. The connector
  returned no HTTP latency or instance-count series; service metadata
  confirms one instance. These samples do not establish peak usage.
- Dashboard environment names inspected: ten existing variables, no
  Naiman Sharga, TransBank, self-ping interval, cadence or proxy overrides;
  no linked environment groups or secret files. Code defaults therefore
  provide the corrected URLs, cadence and 600-second self-ping interval.

### Verification-discovered correction

Swagger at `/` exposed only `APIKeyHeader: X-Admin-Key` for both admin
and FX endpoints. Both dependencies had FastAPI's default scheme name,
so one overwrote the other in OpenAPI. Explicit `AppKey` and `AdminKey`
names correct Swagger/generated-client authentication without changing
runtime auth, public response fields or schema version. Updated
`docs/openapi.json`, the mobile integration prompt and architecture log.
The new regression check first failed with `X-Admin-Key != X-App-Key`.

- [ ] Validate and deploy this small OpenAPI correction.
- [ ] Verify authenticated app/admin reads and private FX caching.
- [x] Read current Render Billing total and finish configuration evidence.

Dashboard read at approximately 11:44 UTC / 20:44 Seoul: **708.23 of
750 free instance hours**, leaving **41.77 hours**. It lists one service;
bandwidth 416 MB/5 GB, pipeline minutes 3/500, and projected October
charges $0.00. These are displayed usage totals, not a promise of final
billing. No cadence reduction or plan change is needed for this release.

OpenAPI follow-up validation: isort, Black, Ruff, **382 tests**, pip check
and whitespace checks passed. The one added test detects header mapping
collisions; the earlier 381-test result describes the maintenance commit.
