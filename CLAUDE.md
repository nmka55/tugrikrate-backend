# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repo.

## Working rules — follow these on every task

1. **Never assume. Verify.** Every claim must trace to a checked fact:
   read the file, fetch the payload, run the command. If something
   cannot be confirmed, **ask** rather than picking a plausible answer.
   Mark unconfirmable facts explicitly (that is what `verified: false`
   is for) instead of guessing. This rule is why four live
   data-correctness bugs were found here rather than shipped — see
   ARCHITECTURE.md §5.
2. **Update ARCHITECTURE.md in the same change as the code.** A change
   is not finished until the doc reflects it. Record the *rationale and
   evidence*, not just the structure.
3. **Log every decision: what, why, how.** The code shows what; it
   never shows why, and why is what is expensive to reconstruct.
4. **Keep the memory current** (`~/.claude/projects/.../memory/`) on
   every prompt and every code change, not batched at the end.
5. **Run the full check sequence before committing** (isort, black,
   ruff, pytest). CI enforces all four.

## What this is

The rates backend for TugrikRate, an iOS MNT currency converter. It
crawls 15 Mongolian banks, stores immutable snapshots, and serves one
stable contract at `GET /v1/rates`. The iOS app is the only consumer.

Forked from MIT-licensed
[btseee/mongolian-bank-exchange-rate](https://github.com/btseee/mongolian-bank-exchange-rate)
(remote: `upstream`, no `origin`).

## The three invariants

Most of this codebase exists to protect these. Breaking one is a
correctness bug, not a style question.

1. **Exactness.** Rates never touch binary floating point. Crawlers
   decode with `json_exact` (`parse_float=Decimal`), parse with
   `app/utils/decimals.parse_decimal`, store as decimal *strings*, and
   serve as JSON strings. If you find yourself writing `float(...)` on
   a rate, stop.
2. **Only real channels.** A quote's `channel`/`side` must be something
   the source actually publishes. Never copy a value between channels,
   never fall back from one to another, never infer a channel from what
   a bank "probably" means. If a source does not say, the channel is
   `unspecified` and that is the honest answer.
3. **Missing is missing.** `None`, `""`, `"-"`, `0` and friends mean the
   source does not publish that number. They never become a value, and
   a missing quote is absent from the response rather than null.

## Architecture

- `app/crawlers/` — one class per bank, from upstream. **Keep these as
  close to upstream as possible** so its "the bank renamed a JSON key"
  fixes cherry-pick cleanly. They still return upstream's fixed
  `{cash:{buy,sell}, noncash:{buy,sell}}` shape.
- `app/sources/registry.py` — what each source *actually* publishes:
  channels, sides, cadence, volatile payload keys, and the **evidence**
  for each claim. Upstream never touches this file, so it never
  conflicts. This is the single source of truth for the feed's labels.
- `app/sources/adapter.py` — reads only the cells a source's registry
  entry declares real and emits `Quote` objects. This is where
  invariant 2 is enforced.
- `app/sources/payload.py` — canonical payload bytes and the hash that
  decides whether a snapshot is written.
- `app/services/collector.py` — per-source crawl with its own session
  and its own try/except. `app/services/scheduler.py` — APScheduler
  cron triggers. `app/services/freshness.py` — cadence and the
  ok/stale/failing rules, shared by both so they cannot drift.
- `app/db/snapshots.py` — insert-on-change, bump-on-no-change.
- `app/api/routers/v1.py` — the public contract. Treat it as frozen;
  `tests/test_v1_contract.py` asserts the wire format key by key. Any
  change here must also update `docs/mobile-integration-prompt.md`,
  which is what the iOS side is built against.

Read `ARCHITECTURE.md` first — it carries the why behind all of this.

## Gotchas

- **`BaseCrawler.parse_float` returns `Decimal`.** The name is kept
  deliberately: all 15 crawlers call it, and identical call sites are
  what make upstream merges painless. Don't "fix" the name.
- **Never hash a raw HTTP body.** Rendered pages carry build ids and
  analytics that change every load; Capitron ships a growing
  `histories` array; SendMN a `trend` field. Use
  `payload_hash(raw, spec.volatile_keys)`. Playwright crawlers must
  `record_payload()` the extracted rows, never the page.
- **Don't add a channel by reading a field name.** Four live
  data-correctness bugs were found by checking payloads instead:
  Capitron's three `rtypecode` rows collapsing to one (publishing its
  non-cash rate as cash), TransBank quoting sides from the customer's
  perspective (its `BUY_RATE` is a sell), the Bank of Mongolia
  reference duplicated into a fake spread, and three sources' cash
  copied into non-cash. Each is documented in the registry entry.
- **`published_at` is only ever what the source states.** Several
  crawlers fall back to an earlier date when today is unpublished;
  that fallback must be reflected in `published_date` so the feed can
  report it as stale rather than passing it off as current.
- **Unit basis is evidence-based.** Every fiat currency is confirmed at
  basis 1 across all sources (`scripts/probe_units.py`). Anything that
  cannot be confirmed ships `verified: false`. Do not widen
  `UNVERIFIED_BASIS_CURRENCIES` down to a guess, or narrow it without
  re-running the probe.
- **Source names are evidence-based.** `name` / `name_mn` /
  `name_evidence` in the registry are pinned by
  `tests/test_source_names.py`. Do not "tidy" or re-translate them
  (TransBank's Mongolian name is `Тээвэр Хөгжлийн Банк`, not a phonetic
  `Транс Банк`). Change one only with new evidence, in the registry,
  the test table and ARCHITECTURE.md together.
- **Frankfurter is source #16 and is not a bank.** It lives in
  `app/crawlers/frankfurter.py` but is deliberately *not* in
  `HTTP_CRAWLERS`, keeping `crawlers/__init__.py` upstream-identical.
  Reference channel only; never derive a rate by crossing through USD;
  use `/v2` (v1 has no MNT); one request per currency, bounded by
  `DailyCallBudget` - the scheduler refuses to start a cadence that
  exceeds `INTL_DAILY_CALL_LIMIT`. Rationale: ARCHITECTURE.md §5.
- **ExchangeRate-API is rejected** (Terms forbid redistribution through
  an API). Do not re-add it; any replacement source must allow
  republishing.
- **Logos are hosted copies with provenance.** Refresh with
  `python -m scripts.fetch_logos`; never hot-link a bank. Naiman
  Sharga intentionally has none (`tests/test_logos.py::NO_LOGO`).
  Set `PUBLIC_BASE_URL` in production.
- **`docs/openapi.json` is committed.** Changing any v1 model fails
  `tests/test_openapi.py` until `python -m scripts.export_openapi` is
  re-run and the diff reviewed. Keep `docs/mobile-integration-prompt.md`
  in step.
- **Line length is 79** (`pyproject.toml`), not black's default 88.
- **`target-version` is pinned to `py313`** even though the Dockerfile
  runs 3.14. Deliberate, inherited from upstream: Black targeting
  `py314` rewrites `except (A, B):` into 3.14-only PEP 758 syntax.
  Don't bump it without re-checking that.
- **No Alembic.** `init_db()` is idempotent; `scripts/migrate_v1.py`
  handles the one-time move off the old `currency_rates` table, which
  is left in place and unused.
- **In-process state.** The rate limiter, the admin job lock and the
  scheduler all assume a single Uvicorn process (no `--workers`). Two
  replicas of the *same* `CRAWL_GROUP` would double every crawl. The
  supported way to run more than one process is `CRAWL_GROUP=fast` on
  the web service plus `CRAWL_GROUP=slow` on a worker: disjoint source
  sets, so no coordination is needed. Anything that changes which
  process owns which source must keep that partition exact - see
  `tests/test_scheduler.py::TestSourceGroups`.
- **Memory.** Upstream hit repeated OOM kills on 512MB from one
  Chromium alongside the HTTP pool. `PLAYWRIGHT_MAX_WORKERS` caps any
  group containing a Playwright source.
- Dependencies are exact-pinned (`==`). Bump deliberately and re-run
  the full check sequence.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

isort app tests scripts main.py --check-only && black app tests scripts main.py --check
ruff check app tests scripts main.py
pytest
```

CI enforces all four. Run them before committing.

## Pulling upstream crawler fixes

```bash
git fetch upstream
git log --oneline HEAD..upstream/main -- app/crawlers/
git cherry-pick <sha>
```

Expect conflicts only in the six crawlers deliberately diverged from
upstream: `mongolbank`, `capitronbank`, `transbank`, `mbank`, `sendmn`,
`naimansharga`. Each has a module docstring saying what was changed and
why — read it before resolving, and keep the divergence.
