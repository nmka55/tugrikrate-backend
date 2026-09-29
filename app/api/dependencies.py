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


def is_app_request(api_key: str | None = Security(_app_key_header)) -> bool:
    """True when the request carries a configured app key.

    Never raises: a missing or wrong key is not an error, it only means
    the caller is not our app, so licence-restricted sources are left
    out of the response. With no APP_API_KEYS configured this is always
    False - restricted data is served to nobody rather than to everyone.
    """
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
