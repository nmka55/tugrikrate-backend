import os

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int = 0) -> int:
    value = _env(key, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _env_positive_int(key: str, default: int) -> int:
    value = _env_int(key, default)
    if value < 1:
        raise ValueError(f"{key} must be greater than or equal to 1")
    return value


def _env_non_negative_int(key: str, default: int) -> int:
    value = _env_int(key, default)
    if value < 0:
        raise ValueError(f"{key} must be greater than or equal to 0")
    return value


def _env_list(key: str, default: str = "") -> list[str]:
    return [
        item.strip() for item in _env(key, default).split(",") if item.strip()
    ]


def _validate_cors_config(origins: list[str], allow_credentials: bool) -> None:
    if allow_credentials and "*" in origins:
        raise ValueError(
            "CORS_ALLOW_CREDENTIALS cannot be true when "
            "CORS_ORIGINS includes *"
        )


def _database_url() -> str:
    default = "sqlite:///./exchange_rates.db"
    url = _env("DATABASE_URL", default) or default
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    # Database
    DATABASE_URL = _database_url()

    # Scheduler. Crawls run on a fast cadence during Mongolian banking
    # hours and an hourly one outside them, both in local time - banks
    # republish on their own working day, not on UTC.
    SCHEDULER_ENABLED = _env_bool("SCHEDULER_ENABLED", True)
    # Which source group this process collects. "all" (default) keeps
    # everything in one process. Splitting into CRAWL_GROUP=fast for the
    # web service and CRAWL_GROUP=slow for a worker puts the five
    # headless-Chromium sources in their own memory space - the two
    # groups own disjoint sources, so they never write the same rows.
    CRAWL_GROUP = _env("CRAWL_GROUP", "all").strip().lower()
    if CRAWL_GROUP not in ("all", "fast", "slow"):
        raise ValueError("CRAWL_GROUP must be one of: all, fast, slow")
    CRAWL_TIMEZONE = _env("CRAWL_TIMEZONE", "Asia/Ulaanbaatar")
    CRAWL_ACTIVE_START_HOUR = _env_non_negative_int(
        "CRAWL_ACTIVE_START_HOUR", 8
    )
    CRAWL_ACTIVE_END_HOUR = _env_non_negative_int("CRAWL_ACTIVE_END_HOUR", 20)
    CRAWL_ACTIVE_INTERVAL_MINUTES = _env_positive_int(
        "CRAWL_ACTIVE_INTERVAL_MINUTES", 15
    )
    CRAWL_OFFPEAK_INTERVAL_MINUTES = _env_positive_int(
        "CRAWL_OFFPEAK_INTERVAL_MINUTES", 60
    )
    # Spread each fire randomly over +/- this many seconds so a bank
    # never sees requests land on an exact 15-minute boundary.
    CRAWL_JITTER_SECONDS = _env_non_negative_int("CRAWL_JITTER_SECONDS", 90)
    # Playwright sources cost a headless Chromium each, so they run on
    # a multiple of the base interval (4 => hourly while active).
    CRAWL_PLAYWRIGHT_MULTIPLIER = _env_positive_int(
        "CRAWL_PLAYWRIGHT_MULTIPLIER", 4
    )
    # How far back a source may look for its most recent publication
    # before giving up (Naiman Sharga has 3-day gaps).
    SOURCE_LOOKBACK_DAYS = _env_non_negative_int("SOURCE_LOOKBACK_DAYS", 7)

    # International foreign-exchange sources (Frankfurter, fxratesapi).
    # Each publishes
    # a daily table, so it is fetched INTL_CRAWLS_PER_DAY times a day -
    # never on the banks' 15-minute cadence - one request per fetch.
    # 4 (every 6 h) is the owner's chosen ceiling. It must divide 24 so
    # the fetches are evenly spaced.
    INTL_CRAWLS_PER_DAY = _env_positive_int("INTL_CRAWLS_PER_DAY", 4)
    if 24 % INTL_CRAWLS_PER_DAY:
        raise ValueError("INTL_CRAWLS_PER_DAY must divide 24 evenly")
    # Hard backstop on outbound requests per UTC day, enforced in the
    # crawler (app/utils/call_budget.py) and checked against the
    # schedule when the scheduler starts. Frankfurter documents no cap,
    # so this is a self-imposed one; equal to the schedule, so a manual
    # admin crawl beyond it is refused rather than sent.
    INTL_DAILY_CALL_LIMIT = _env_positive_int("INTL_DAILY_CALL_LIMIT", 4)
    # fxratesapi.com key (registered free plan). Empty = the source is
    # disabled: never scheduled, never served. Never fall back to the
    # keyless public plan, which its FAQ says is not for production.
    FXRATESAPI_KEY = _env("FXRATESAPI_KEY").strip()
    # Keys the iOS app sends as `X-App-Key`. Sources whose licence allows
    # showing their data only inside our own app (fxratesapi) are served
    # solely to a request carrying one of these. Comma list, so a key can
    # be rotated without breaking installed app versions. Empty means
    # restricted data is served to nobody (fail closed).
    APP_API_KEYS = _env_list("APP_API_KEYS")

    # Optional comma list restricting which currencies are published
    # from the table. Empty means every currency it lists (minus MNT
    # and the four metals).
    FRANKFURTER_CURRENCIES = _env_list("FRANKFURTER_CURRENCIES")

    # Freshness thresholds behind the v1 `status` field. A source is
    # `ok` while it succeeded within this multiple of its own interval,
    # `stale` after that, and `failing` once it has missed this many
    # consecutive attempts.
    STALE_AFTER_INTERVALS = _env_positive_int("STALE_AFTER_INTERVALS", 3)
    FAILING_AFTER_ATTEMPTS = _env_positive_int("FAILING_AFTER_ATTEMPTS", 3)
    # A source can be perfectly reachable while serving rates it
    # published days ago (Naiman Sharga runs 2-3 day gaps). When a
    # source states its publication date and that date is older than
    # this, it is reported stale even though the crawl succeeded.
    PUBLISHED_STALE_HOURS = _env_positive_int("PUBLISHED_STALE_HOURS", 36)

    # HTTP settings
    SSL_VERIFY = _env_bool("SSL_VERIFY", True)
    REQUEST_TIMEOUT = _env_positive_int("REQUEST_TIMEOUT", 30)
    PLAYWRIGHT_TIMEOUT = _env_positive_int("PLAYWRIGHT_TIMEOUT", 60000)

    # Logging
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")

    # Self-ping keepalive (empty means disabled). Set to the app's own public
    # URL (e.g. https://your-app.onrender.com) to stop Render's free tier
    # from sleeping the instance after ~15 minutes of no inbound traffic.
    SELF_PING_URL = _env("SELF_PING_URL")
    SELF_PING_INTERVAL_SECONDS = _env_positive_int(
        "SELF_PING_INTERVAL_SECONDS", 30
    )

    # Absolute origin used to build `logo_url` (e.g.
    # https://api.example.com). Empty falls back to the request's own
    # origin, which behind a TLS-terminating proxy is often http://
    # rather than https:// - set this in production so the iOS app is
    # never handed a cleartext image URL.
    PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL").rstrip("/")

    # Public API safeguards
    API_MAX_LIMIT = _env_positive_int("API_MAX_LIMIT", 100)
    CORS_ORIGINS = _env_list("CORS_ORIGINS", "*")
    CORS_ALLOW_CREDENTIALS = _env_bool("CORS_ALLOW_CREDENTIALS", False)
    _validate_cors_config(CORS_ORIGINS, CORS_ALLOW_CREDENTIALS)
    TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)
    RATE_LIMIT_ENABLED = _env_bool("RATE_LIMIT_ENABLED", True)
    RATE_LIMIT_REQUESTS = _env_positive_int("RATE_LIMIT_REQUESTS", 60)
    RATE_LIMIT_WINDOW_SECONDS = _env_positive_int(
        "RATE_LIMIT_WINDOW_SECONDS", 60
    )
    RATE_LIMIT_MAX_CLIENTS = _env_positive_int("RATE_LIMIT_MAX_CLIENTS", 10000)

    # Admin endpoints (POST /api/admin/*) - empty means the feature is
    # disabled (503), never "open to anyone"
    ADMIN_API_KEY = _env("ADMIN_API_KEY")

    # Parallel execution
    ENABLE_PARALLEL = _env_bool("ENABLE_PARALLEL", True)
    MAX_WORKERS = _env_positive_int("MAX_WORKERS", 8)
    PLAYWRIGHT_MAX_WORKERS = _env_positive_int("PLAYWRIGHT_MAX_WORKERS", 3)

    # Snapshot retention. 0 keeps everything; snapshots are written only
    # when rates actually change, so growth is modest either way.
    SNAPSHOT_RETENTION_DAYS = _env_non_negative_int(
        "SNAPSHOT_RETENTION_DAYS", 0
    )

    # Bank API endpoints
    KHANBANK_URI = _env(
        "KHANBANK_URI", "https://www.khanbank.com/api/back/rates"
    )
    GOLOMT_URI = _env("GOLOMT_URI", "https://www.golomtbank.com/api/exchange")
    XACBANK_URI = _env("XACBANK_URI", "https://xacbank.mn/api/currencies")
    ARIGBANK_API_URL = _env(
        "ARIGBANK_API_URL", "https://www.arigbank.mn/exchange/getRate"
    )
    ARIGBANK_SIGNIN_URL = _env(
        "ARIGBANK_SIGNIN_URL", "https://www.arigbank.mn/exchange/signIn"
    )
    ARIGBANK_BEARER_TOKEN = _env("ARIGBANK_BEARER_TOKEN")
    STATEBANK_URI = _env(
        "STATEBANK_URI", "https://www.statebank.mn/back/api/fetchrate"
    )
    MONGOLBANK_URI = _env(
        "MONGOLBANK_URI",
        "https://www.mongolbank.mn/en/currency-rate-movement/data",
    )
    CAPITRONBANK_API_URL = _env(
        "CAPITRONBANK_API_URL",
        "https://www.capitronbank.mn/admin/en/wp-json/bank/rates/capitronbank",
    )

    # International reference API (v2: v1 is ECB-only and has no MNT)
    FRANKFURTER_URI = _env("FRANKFURTER_URI", "https://api.frankfurter.dev/v2")
    FXRATESAPI_URI = _env("FXRATESAPI_URI", "https://api.fxratesapi.com")

    # Playwright-based bank URLs
    TDBM_URI = _env("TDBM_URI", "https://www.tdbm.mn/en/exchange-rates")
    BOGDBANK_URI = _env("BOGDBANK_URI", "https://www.bogdbank.com/exchange")
    CKBANK_URI = _env("CKBANK_URI", "https://www.ckbank.mn/currency-rates")
    NIBANK_URI = _env("NIBANK_URI", "https://www.nibank.mn/en/rate")
    TRANSBANK_URI = _env("TRANSBANK_URI", "https://transbank.mn/en/exchange")
    MBANK_URI = _env("MBANK_URI", "https://m-bank.mn/")

    # Firebase Firestore-based APIs
    SENDMN_FIRESTORE_URL = _env(
        "SENDMN_FIRESTORE_URL",
        "https://firestore.googleapis.com/v1/projects/sendmn-remit/databases/(default)/documents/rate/exrate?key=AIzaSyCrYSDevTZ_WigjBGeOuoGj-ntLO5v9taY",
    )
    NSHARGA_FIRESTORE_BASE_URL = _env(
        "NSHARGA_FIRESTORE_BASE_URL",
        "https://firestore.googleapis.com/v1/projects/nsharga-2ec6a/databases/(default)/documents/currency_rates?key=AIzaSyBA-tL_dqi1MsO43OkwllUpV7k6rf0Iys4",
    )


config = Config()
