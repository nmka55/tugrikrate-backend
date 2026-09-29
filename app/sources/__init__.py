"""Per-source facts, normalization, and payload hashing."""

from app.sources.adapter import collect, result_hash
from app.sources.models import (
    CHANNEL_CASH,
    CHANNEL_NONCASH,
    CHANNEL_REFERENCE,
    CHANNEL_UNSPECIFIED,
    SIDE_BUY,
    SIDE_REFERENCE,
    SIDE_SELL,
    CrawlResult,
    Quote,
)
from app.sources.payload import canonical_payload, payload_hash
from app.sources.registry import BY_ID, SPECS, SourceSpec

__all__ = [
    "BY_ID",
    "SPECS",
    "SourceSpec",
    "Quote",
    "CrawlResult",
    "collect",
    "result_hash",
    "payload_hash",
    "canonical_payload",
    "CHANNEL_CASH",
    "CHANNEL_NONCASH",
    "CHANNEL_REFERENCE",
    "CHANNEL_UNSPECIFIED",
    "SIDE_BUY",
    "SIDE_SELL",
    "SIDE_REFERENCE",
]
