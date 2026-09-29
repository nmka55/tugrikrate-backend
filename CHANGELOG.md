<!-- markdownlint-disable MD024 -->

# Өөрчлөлтийн Түүх

## [Unreleased]

### Added

- **Frankfurter** as a 16th source (`type: "international_aggregator"`)
  and a new endpoint, **`GET /v1/fx`**: a USD-based foreign-exchange
  table (160 currencies, units of each per 1 USD, no MNT) for converting
  foreign↔foreign and for the USD leg of the fallback in ARCHITECTURE.md
  "Conversion policy". Uses Frankfurter `/v2` (`/v1` has no MNT). One
  request per fetch, **4 fetches a day** (00/06/12/18 Ulaanbaatar), with
  a 4-request daily ceiling that the scheduler checks at startup. New
  config: `INTL_CRAWLS_PER_DAY`, `INTL_DAILY_CALL_LIMIT`,
  `FRANKFURTER_CURRENCIES`, `FRANKFURTER_URI`. It is deliberately not on
  `/v1/rates`, whose `rate` always means MNT per unit.
- `kind` (`mnt_rates` | `usd_table`) on `/v1/sources`.
- The owner's conversion policy, recorded as a requirement.
- `logo_url` on every source in `GET /v1/rates` and `/v1/sources`
  (null for Naiman Sharga, whose logo provenance could not be
  established). Logos are hosted at `/static/logos/` with a provenance
  manifest; refresh with `python -m scripts.fetch_logos`. New config:
  `PUBLIC_BASE_URL` (set it in production).
- `docs/openapi.json` (regenerate with `python -m scripts.export_openapi`)
  and a test that fails when it is stale. `/v1/sources` and history now
  have response models; `/v1/rates` documents `ETag` and 304.

- `name_mn` (official Mongolian name) on every source in
  `GET /v1/rates` and `/v1/rates/{id}/history`; `name_evidence` on
  `/v1/sources`. Additive, `schema_version` remains 1.

### Changed

- Source names corrected to each institution's own official form:
  TransBank is `Тээвэр Хөгжлийн Банк` in Mongolian (was `Транс Банк`);
  `State Bank` -> `State Bank of Mongolia`; `Trade and Development
  Bank` -> `... of Mongolia`; `National Investment Bank` -> `... of
  Mongolia`; `Хас Банк` -> `ХасБанк`; `М Банк` -> `М банк`; Naiman
  Sharga and SendMN Mongolian names. Evidence in ARCHITECTURE.md §5.
- The ETag now covers source names and logo paths, so a rename or a
  replaced logo reaches cached clients.
- `RatesResponse.schema_version` is now a required field in the OpenAPI
  schema (the value is unchanged, still 1).
- The rate limiter no longer counts `/static/` requests.

### Rejected

- ExchangeRate-API: its Terms forbid re-distribution and use in any
  service offering programmatic access to rates. See ARCHITECTURE.md §5.
- Viv Data "Currency Converter API" (API.market): a resale of the same
  ExchangeRate-API data, so the same restriction applies. See §5.

## [2.0.0] - 2026-09-29

Fork of [btseee/mongolian-bank-exchange-rate](https://github.com/btseee/mongolian-bank-exchange-rate)
(MIT, Battseren Badral) into the TugrikRate rates backend. Breaking:
the entire public API is replaced. Entries below this one are the
upstream project's history, in Mongolian.

### Data correctness

Four live labelling bugs, found by inspecting bank payloads directly:

- **Capitron Bank** returns three rows per currency keyed by
  `rtypecode` (1 reference, 2 cash, 3 non-cash). The old loop wrote
  `rates[code]` for each, so the last row won and was copied into both
  channels - publishing the non-cash rate as the cash rate and
  discarding the real cash rate. Now mapped by rate type.
- **Trans Bank** labels sides from the customer's perspective: USD cash
  reads `BUY_RATE` 3617 / `SELL_RATE` 3587, the reverse of every other
  source. Taken literally it made Trans Bank look like the best buy
  rate in the country. Now normalised to the bank's perspective.
- **Bank of Mongolia** publishes one official reference rate per
  currency; it was duplicated into `noncash.buy` and `noncash.sell`,
  inventing a zero-width spread. Now `channel: "reference"`.
- **M Bank, SendMN, Naiman Sharga** publish a single unlabelled pair
  that was copied into both channels. Now reported once as
  `channel: "unspecified"`.

### Reliability

- **Khan Bank was failing entirely** with an SSL handshake error. Its
  server only offers `AES256-SHA256`, which OpenSSL 3.x rejects at the
  default security level. Fixed with a per-session cipher setting;
  certificate verification is unchanged.
- **Naiman Sharga** publishes with 2-3 day gaps, which the old
  single-day fallback could not cover, so the source vanished from the
  feed. Now walks back `SOURCE_LOOKBACK_DAYS` and reports the date it
  actually landed on via `published_at`.

### Added

- `rate_snapshots`: immutable history, one row per distinct payload
  hash. Unchanged crawls bump `last_checked_at` instead of inserting.
- Crawls every 15 min 08:00-20:00 Asia/Ulaanbaatar, hourly outside,
  with random jitter. Playwright sources on a configurable multiple.
- `GET /v1/rates` - the stable contract. Rates as decimal strings,
  per-quote `channel`/`side`/`unit_basis`/`verified`, per-source
  `status` of ok/stale/failing, ETag/304 support.
- `GET /v1/sources` and `GET /v1/rates/{id}/history`.
- `app/sources/registry.py`, recording what each source publishes and
  the evidence behind every claim.
- `scripts/probe_units.py`, which re-derives each source's unit basis
  from live data. Every fiat currency confirmed at basis 1 across all
  sources; precious metals and KPW ship `verified: false`.
- `scripts/migrate_v1.py`.
- `CRAWL_GROUP` (`all`/`fast`/`slow`) and `scripts/worker.py`, so the
  five Playwright sources can run in their own process. The groups own
  disjoint sources and share only the database, so they need no
  coordination. `docker compose up` runs the split by default.

### Changed

- Rates are exact `Decimal` end to end, stored and served as strings.
  `parse_float` no longer returns a float (the name is retained so
  upstream crawler fixes keep merging cleanly).
- Locale-independent number parsing; malformed grouping such as
  `"3450.50.50"` now reports missing instead of silently becoming
  34,505,050.
- Per-source crawl isolation: own session, own try/except. A failure
  never touches the last good snapshot.
- `schedule` replaced by APScheduler (timezone support and jitter).

### Removed

- `GET /api/rates/*` and the `currency_rates` one-row-per-day table.
  The table is left in place but unused; its rows carry the labelling
  errors above and are deliberately not migrated.
- Backfill: banks do not serve historical intraday rates, so there is
  nothing to backfill under snapshot semantics.
- The GitHub Actions scheduled-crawl workflow, redundant now that the
  scheduler runs in-process.


## [v1.1.0] - 2026-07-30

Heroku-г бүрмөсөн хасаж, Render.com-ийн free Docker web service рүү шилжсэн том refactor. **API restructure нь breaking change** - өмнөх unprefixed зам (`/rates`, `/health`) ашиглаж байсан клиентүүд `/api/` prefix рүү шилжих шаардлагатай.

### Нэмсэн

- Swagger docs `/` root дээр, бүх функциональ endpoint `/api/` prefix-тэй болсон (өмнөх root info endpoint нь `GET /api/info`).
- Admin endpoint-ууд: `POST /api/admin/crawl`, `crawl/{bank_name}`, `backfill`, `GET /api/admin/status` - `X-Admin-Key` header-ээр хамгаалагдсан.
- `render.yaml` Blueprint болон `.github/workflows/scheduled-crawl.yml` - Render free tier дээр always-on worker байхгүй тул admin endpoint-ийг өдөр бүр дуудаж амжилтыг баталгаажуулна.
- `SELF_PING_URL` тохиргоо - тохируулбал өөрийгөө тогтмол зайтай ping хийж Render instance-ийг idle-аар унтахаас сэргийлнэ.
- `CurrencyRate` дээр `(bank_name, date)` unique constraint, атом upsert (`INSERT ... ON CONFLICT`) - давхардсан мөр үүсэх race condition арилсан.
- Crawler-уудад retry (`requests` `Retry`/`HTTPAdapter`) болон нийтлэг browser-like `User-Agent`; `0` валют татсан тохиолдолд `WARNING` log.
- Backfill-ийн өдөр бүрийн хооронд `BACKFILL_DELAY_SECONDS` азнах логик.

### Өөрчилсөн

- Python 3.14 дээр стандартчилагдсан (өмнө нь 3.11/3.13/3.14 холилдсон байсан); `requirements.txt` бүх хамаарал `==` тэмдэгтээр тогтмол хувилбарт лацдагдсан.
- `psycopg2-binary` → `psycopg[binary]` (psycopg3); `SSL_VERIFY` default `false` → `true`.
- `MBank` crawler зөв `HTTP_CRAWLERS` бүлэгт орсон (өмнө нь илүү удаан Playwright бүлэгт байсан).
- `.env.example` хялбарчлагдсан - бүх банкны URL нь `app/config.py`-д код defaults болсон тул `.env`-д давхардуулах шаардлагагүй болсон.
- `ScraperService.run_all()`/`backfill()` одоо гүйцэтгэлийн тойм (`succeeded`/`failed`/`failed_banks`) буцаадаг.
- README, CHANGELOG хялбарчлагдсан.

### Зассан

Бүх 15 банкыг `2026-07-30`-ны өдөр live crawl хийж баталгаажуулахад 3 банк бодитоор эвдэрсэн болохыг илрүүлж засав (15/15 амжилттай):

- **MongolBank** - API өнөөдрийн ханшийг хараахан нийтлээгүй үед хамгийн сүүлийн боломжит мөрд буцаж очих fallback болсон.
- **BogdBank** - валютын код баганын текст `<img>` флаг болсон тул зурагны файлын нэрнээс кодыг гаргаж авдаг болсон.
- **TransBank** - `wait_until="networkidle"` бараг үргэлж timeout өгдөг байсныг `domcontentloaded` болгосон.

### Хассан

- `bin/post_compile` (Heroku buildpack hook), ашиглагдаагүй Docker `./logs` volume, `[tool.flake8]` тохиргоо, ашиглагдаагүй `Pygments` хамаарал.

## [v1.0.9] - 2026-05-06

`v1.0.8`-ийн банкны live API өөрчлөлт, Heroku worker import асуудлаас болж тогтворгүй байсныг засаж тогтвортой дахин гаргасан хувилбар.

- Public API хамгаалалт нэмэгдсэн: configurable CORS, pagination дээд хэмжээ, rate limit, `Retry-After`/`X-RateLimit-*` header.
- StateBank, CapitronBank, MongolBank, TDBM, ArigBank, NaimanSharga-ийн upstream response өөрчлөлтөөс болсон parsing алдаанууд засагдсан.
- `.env.example` анх удаа нэмэгдсэн; README/CONTRIBUTING шинэчлэгдсэн.
- `2026-05-06`: 15/15 банк амжилттай, бүх шалгалт ногоон.

## [v1.0.8] - 2026-05-05

> Тогтворгүй болсон тул `v1.0.9`-ээр солигдсон (ArigBank token expiry, NaimanSharga Firestore URL, MongolBank JSON endpoint, StateBank/CapitronBank response drift, TDBM timeout, Heroku worker import).

- **NaimanSharga**, **SendMN** crawler-ууд нэмэгдсэн (Firebase Firestore-based).
- MongolBank XXE эмзэг байдал, BogdBank/TDBM/TransBank/MBank/CapitronBank-ийн бодит алдаанууд засагдсан.
- Нийт дэмжигдэх банкны тоо: 13 → 15.

## [v1.0.7] - 2026-03-04

- ArigBank bearer token, MBank parser засагдсан; `MBank` HTTP crawler руу шилжсэн.
- Дутуу байсан `__init__.py` файлууд нэмэгдсэн; Dockerfile-ийн Python хувилбар 3.13 болсон.
- CodeQL code scanning workflow нэмэгдсэн.

## [v1.0.6] - 2026-02-06

Код бүтцийг бүхэлд нь сайжруулсан refactor: crawler-уудыг нэгтгэж давхардал арилгасан, base crawler class, хялбаршуулсан scraper service, цэгцтэй API endpoint. CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, issue/PR template, энэ CHANGELOG нэмэгдсэн.

## [v1.0.5] - 2026-01-22

- Heroku deployment бэлтгэл: Playwright APT buildpack, Procfile, runtime config.
- Давхардсан мөр үүсэхээс сэргийлэх upsert логик, backfill script нэмэгдсэн.
- BogdBank-ийн түүхэн огнооны (`date` параметргүй) crawl засагдсан.
- Playwright дэмжлэгийн тулд Docker container deployment руу шилжсэн.

## [v1.0.4] - 2026-01-22

Анхны бодит test suite, CI/CD pipeline, lint/formatting (isort/black) тохиргоо нэмэгдсэн. CodeQL action, gh-release action шинэчлэгдсэн.

## [v1.0.3] - 2026-01-20

Код цэвэрлэгээ - ашиглагдаагүй comment/log устгасан.

## [v1.0.2] - 2025-11-05

- Parallel processing (зэрэгцээ crawl) нэмэгдсэн; TDBM-ийн Playwright timeout засагдсан.
- CI workflow-уудыг хялбарчилсан, crawler бүтцийг сайжруулсан, код форматлагдсан.

## [v1.0.1] - 2025-10-31

Docker тохиргооны жижиг засвар.

## [v1.0.0] - 2025-10-31

Анхны хувилбар - 13 банкны валютын ханш цуглуулалт, FastAPI REST API, Docker, GitHub Actions CI/CD анх удаа тохируулагдсан.
