"""Shared FastAPI dependencies: source enum and admin auth."""

import secrets
from enum import Enum

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import config
from app.sources.registry import SPECS

SOURCE_IDS = [spec.id for spec in SPECS]
SourceId = Enum("SourceId", {spec.id: spec.id for spec in SPECS})

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


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
