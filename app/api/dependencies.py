"""Shared FastAPI dependencies: source enum, admin and app auth."""

import secrets
from enum import Enum

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import config
from app.sources.registry import SPECS

SOURCE_IDS = [spec.id for spec in SPECS]
SourceId = Enum("SourceId", {spec.id: spec.id for spec in SPECS})

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)
_app_key_header = APIKeyHeader(
    name="X-App-Key",
    auto_error=False,
    description=(
        "Identifies the TugrikRate iOS app. Optional: without it the "
        "response omits sources whose licence restricts them to our own "
        "app."
    ),
)


def app_keys_enforced() -> bool:
    """False only in development mode: no APP_API_KEYS and no
    REQUIRE_APP_KEY. REQUIRE_APP_KEY with no keys is refused at startup
    (app/config.py); it is also checked here so that state can never
    mean "open" even if it is reached some other way."""
    return bool(config.APP_API_KEYS) or config.REQUIRE_APP_KEY


def is_app_request(api_key: str | None = Security(_app_key_header)) -> bool:
    """True when licence-restricted sources may go into this response.

    Never raises: a missing or wrong key is not an error, it only means
    the caller is not our app, so restricted sources are left out. In
    development mode (no keys configured, not required) every request
    qualifies - see APP_API_KEYS in app/config.py for why that is only
    acceptable off the public internet.
    """
    if not app_keys_enforced():
        return True
    if not api_key:
        return False
    return any(
        secrets.compare_digest(api_key, key) for key in config.APP_API_KEYS
    )


def require_admin_key(api_key: str = Security(_admin_key_header)) -> None:
    """Guard for /api/admin/*. An unset ADMIN_API_KEY disables the whole
    surface (503) rather than ever being treated as "no key required"."""
    if not config.ADMIN_API_KEY:
        raise HTTPException(
            503, "Admin endpoints are disabled (ADMIN_API_KEY not set)"
        )
    key_matches = api_key and secrets.compare_digest(
        api_key, config.ADMIN_API_KEY
    )
    if not key_matches:
        raise HTTPException(401, "Invalid or missing X-Admin-Key header")
