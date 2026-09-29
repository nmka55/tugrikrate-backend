"""The v1 public contract - the only thing the iOS app reads.

Stability rules for this module:

- `schema_version` is 1. Any change that could break a client bumps it.
- Rates and unit bases are JSON **strings** ("3450.50"), never numbers.
  A JSON number would be parsed as a double by every client and silently
  lose the precision the whole pipeline exists to preserve.
- A missing quote is absent from `quotes`. There is no null rate and no
  zero standing in for "not published".
- Timestamps are UTC, suffixed `Z`.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import config
from app.db import snapshots as snapshot_repo
from app.db.database import get_db
from app.models.snapshot import RateSnapshot
from app.services.freshness import compute_status
from app.sources.logos import logo_path
from app.sources.registry import BY_ID, KIND_MNT_RATES, KIND_USD_TABLE, SPECS

router = APIRouter(prefix="/v1", tags=["v1"])

SCHEMA_VERSION = 1

FX_BASE_CURRENCY = "USD"
MNT_SPECS = tuple(s for s in SPECS if s.kind == KIND_MNT_RATES)
FX_SPECS = tuple(s for s in SPECS if s.kind == KIND_USD_TABLE)


def _iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class QuoteOut(BaseModel):
    currency: str = Field(examples=["USD"])
    channel: str = Field(
        description=(
            "cash | noncash | reference | unspecified. `unspecified` "
            "means the source publishes one rate pair without saying "
            "which channel it applies to - it is not a guess. (The "
            "history of a foreign-exchange source also shows "
            "`usd_table`; that source is served by /v1/fx, not here.)"
        ),
        examples=["noncash"],
    )
    side: str = Field(
        description=(
            "buy | sell | reference, always from the source's "
            "perspective: `buy` is what it pays you."
        ),
        examples=["sell"],
    )
    rate: str = Field(
        description=(
            "Decimal string. MNT per `unit_basis` units (on /v1/rates)."
        ),
        examples=["3450.00"],
    )
    unit_basis: str = Field(
        description="How many units of the currency `rate` prices.",
        examples=["1"],
    )
    verified: bool = Field(
        description=(
            "False when the quoting unit could not be confirmed - "
            "treat the rate as indicative only."
        ),
        examples=[True],
    )


class SourceOut(BaseModel):
    id: str = Field(examples=["khanbank"])
    name: str = Field(
        description="Official English name.", examples=["Khan Bank"]
    )
    name_mn: str = Field(
        description="Official Mongolian (Cyrillic) name.",
        examples=["Хаан Банк"],
    )
    logo_url: Optional[str] = Field(
        description=(
            "Absolute URL of this source's logo (PNG or JPEG, square "
            "where the source has an app icon). Null when no logo of "
            "verified provenance exists. The URL embeds a content hash, "
            "so it can be cached indefinitely."
        ),
        examples=[
            "https://api.example.com/static/logos/khanbank.jpg?v=ab12cd34ef56"
        ],
    )
    type: str = Field(
        description=(
            "commercial_bank | central_bank | exchange_bureau | "
            "remittance | international_aggregator. Treat as "
            "non-exhaustive."
        ),
        examples=["commercial_bank"],
    )
    status: str = Field(
        description=(
            "ok | stale | failing. A stale or failing source still "
            "returns its last good quotes."
        ),
        examples=["ok"],
    )
    fetched_at: Optional[datetime] = Field(
        description="When this service retrieved these rates."
    )
    published_at: Optional[datetime] = Field(
        description=(
            "When the source says it published them. Null when the "
            "source does not say - never inferred from fetch time."
        )
    )
    last_checked_at: Optional[datetime] = Field(
        description="Most recent crawl that returned this same payload."
    )
    quotes: list[QuoteOut]

    @field_serializer("fetched_at", "published_at", "last_checked_at")
    def _serialize_ts(self, value: datetime | None) -> str | None:
        return _iso_z(value)


class RatesResponse(BaseModel):
    # No default: a default makes OpenAPI list the field as optional,
    # and a generated Swift client would then type it `Int?`.
    schema_version: int = Field(
        description="Contract version. Bumped on any breaking change.",
        examples=[1],
    )
    generated_at: datetime
    sources: list[SourceOut]

    @field_serializer("generated_at")
    def _serialize_generated(self, value: datetime) -> str:
        return _iso_z(value)


class FxRateOut(BaseModel):
    currency: str = Field(examples=["KZT"])
    rate: str = Field(
        description=(
            "Decimal string. Units of `currency` that 1 US dollar buys. "
            "No MNT is involved anywhere on this endpoint."
        ),
        examples=["441.22"],
    )
    verified: bool = Field(
        description="False when the quoting unit could not be confirmed.",
        examples=[True],
    )


class FxSourceOut(BaseModel):
    id: str = Field(examples=["frankfurter"])
    name: str = Field(examples=["Frankfurter"])
    name_mn: str = Field(examples=["Франкфуртер"])
    logo_url: Optional[str]
    type: str = Field(examples=["international_aggregator"])
    status: str = Field(
        description=(
            "ok | stale | failing. A stale or failing source still "
            "returns its last good table."
        ),
        examples=["ok"],
    )
    fetched_at: Optional[datetime]
    published_at: Optional[datetime] = Field(
        description="Newest date the source states across its pairs."
    )
    last_checked_at: Optional[datetime]
    rates: list[FxRateOut]

    @field_serializer("fetched_at", "published_at", "last_checked_at")
    def _serialize_ts(self, value: datetime | None) -> str | None:
        return _iso_z(value)


class FxResponse(BaseModel):
    schema_version: int = Field(
        description="Contract version. Bumped on any breaking change.",
        examples=[1],
    )
    generated_at: datetime
    base: str = Field(
        description="Every rate is quoted against this currency.",
        examples=["USD"],
    )
    sources: list[FxSourceOut]

    @field_serializer("generated_at")
    def _serialize_generated(self, value: datetime) -> str:
        return _iso_z(value)


class SourceInfoOut(BaseModel):
    id: str = Field(examples=["khanbank"])
    name: str
    name_mn: str
    name_evidence: str = Field(
        description="Where the two names came from (for re-checking)."
    )
    logo_url: Optional[str]
    type: str
    cadence: str = Field(description="fast | slow | daily", examples=["fast"])
    kind: str = Field(
        description=(
            "mnt_rates (served on /v1/rates: `rate` is MNT per unit) | "
            "usd_table (served on /v1/fx: `rate` is units per 1 USD)."
        ),
        examples=["mnt_rates"],
    )
    channels: list[str] = Field(examples=[["cash", "noncash"]])
    evidence: str = Field(
        description="How each channel/side label was confirmed."
    )


class SourcesResponse(BaseModel):
    schema_version: int
    sources: list[SourceInfoOut]


class HistorySourceOut(BaseModel):
    id: str
    name: str
    name_mn: str
    type: str


class HistorySnapshotOut(BaseModel):
    fetched_at: Optional[str]
    published_at: Optional[str]
    last_checked_at: Optional[str]
    payload_hash: str
    quotes: list[QuoteOut]


class HistoryResponse(BaseModel):
    schema_version: int
    source: HistorySourceOut
    snapshots: list[HistorySnapshotOut]


def _filter_quotes(raw: list, currencies: set[str] | None) -> list[dict]:
    quotes = raw or []
    if currencies:
        quotes = [q for q in quotes if q.get("currency") in currencies]
    return quotes


def _parse_currencies(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    codes = {c.strip().upper() for c in raw.split(",") if c.strip()}
    return codes or None


def _logo_url(source_id: str, base_url: str) -> str | None:
    path = logo_path(source_id)
    return f"{base_url}{path}" if path else None


def _build_payload(
    db: Session,
    currencies: set[str] | None,
    source_ids: set[str] | None,
    base_url: str = "",
) -> RatesResponse:
    latest = snapshot_repo.latest_snapshots(db)
    states = snapshot_repo.source_states(db)
    now = datetime.now(timezone.utc)

    sources = []
    for spec in MNT_SPECS:
        if source_ids and spec.id not in source_ids:
            continue
        snapshot = latest.get(spec.id)
        sources.append(
            SourceOut(
                id=spec.id,
                name=spec.name,
                name_mn=spec.name_mn,
                logo_url=_logo_url(spec.id, base_url),
                type=spec.type,
                status=compute_status(
                    spec, snapshot, states.get(spec.id), now
                ),
                fetched_at=snapshot.fetched_at if snapshot else None,
                published_at=snapshot.published_at if snapshot else None,
                last_checked_at=(
                    snapshot.last_checked_at if snapshot else None
                ),
                quotes=[
                    QuoteOut(**q)
                    for q in _filter_quotes(
                        snapshot.quotes if snapshot else [], currencies
                    )
                ],
            )
        )

    return RatesResponse(
        schema_version=SCHEMA_VERSION, generated_at=now, sources=sources
    )


def _base_url(request: Request) -> str:
    return config.PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


def _etag(payload: RatesResponse, base_url: str = "") -> str:
    """Identifies the rate content, deliberately excluding
    `generated_at` - otherwise every response would be a new ETag and
    the app could never get a 304."""
    digest = hashlib.sha256()
    for source in payload.sources:
        digest.update(source.id.encode())
        digest.update(source.name.encode())
        digest.update(source.name_mn.encode())
        # Hash the logo path, not the host: the same rates must keep
        # the same ETag whichever address the app reached us on.
        digest.update((source.logo_url or "")[len(base_url) :].encode())
        digest.update(source.status.encode())
        digest.update(str(source.fetched_at).encode())
        for quote in source.quotes:
            digest.update(
                f"{quote.currency}{quote.channel}{quote.side}"
                f"{quote.rate}{quote.unit_basis}{quote.verified}".encode()
            )
    return f'W/"{digest.hexdigest()[:32]}"'


@router.get(
    "/rates",
    response_model=RatesResponse,
    summary="All sources, latest rates",
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Send back as If-None-Match.",
                    "schema": {"type": "string"},
                }
            }
        },
        304: {"description": "Unchanged since the supplied ETag; no body."},
    },
)
def get_rates(
    request: Request,
    response: Response,
    currency: Optional[str] = Query(
        None,
        description="Comma-separated ISO codes, e.g. USD,EUR",
        examples=["USD,EUR"],
    ),
    source: Optional[str] = Query(
        None, description="Comma-separated source ids", examples=["khanbank"]
    ),
    db: Session = Depends(get_db),
):
    """Every source's most recent rates.

    Supports `If-None-Match`: an app polling on an interval gets a bare
    304 when nothing has moved, which matters on a metered connection.
    """
    source_ids = _parse_currencies(source)
    source_ids = {s.lower() for s in source_ids} if source_ids else None
    base_url = _base_url(request)
    payload = _build_payload(
        db, _parse_currencies(currency), source_ids, base_url
    )

    etag = _etag(payload, base_url)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=60"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=dict(response.headers))
    return payload


def _build_fx(
    db: Session,
    currencies: set[str] | None,
    source_ids: set[str] | None,
    base_url: str = "",
) -> FxResponse:
    latest = snapshot_repo.latest_snapshots(db)
    states = snapshot_repo.source_states(db)
    now = datetime.now(timezone.utc)

    sources = []
    for spec in FX_SPECS:
        if source_ids and spec.id not in source_ids:
            continue
        snapshot = latest.get(spec.id)
        sources.append(
            FxSourceOut(
                id=spec.id,
                name=spec.name,
                name_mn=spec.name_mn,
                logo_url=_logo_url(spec.id, base_url),
                type=spec.type,
                status=compute_status(
                    spec, snapshot, states.get(spec.id), now
                ),
                fetched_at=snapshot.fetched_at if snapshot else None,
                published_at=snapshot.published_at if snapshot else None,
                last_checked_at=(
                    snapshot.last_checked_at if snapshot else None
                ),
                rates=[
                    FxRateOut(
                        currency=q["currency"],
                        rate=q["rate"],
                        verified=q["verified"],
                    )
                    for q in _filter_quotes(
                        snapshot.quotes if snapshot else [], currencies
                    )
                ],
            )
        )
    return FxResponse(
        schema_version=SCHEMA_VERSION,
        generated_at=now,
        base=FX_BASE_CURRENCY,
        sources=sources,
    )


def _fx_etag(payload: FxResponse, base_url: str = "") -> str:
    digest = hashlib.sha256()
    for source in payload.sources:
        digest.update(source.id.encode())
        digest.update(source.name.encode())
        digest.update(source.name_mn.encode())
        digest.update((source.logo_url or "")[len(base_url) :].encode())
        digest.update(source.status.encode())
        digest.update(str(source.fetched_at).encode())
        for rate in source.rates:
            digest.update(
                f"{rate.currency}{rate.rate}{rate.verified}".encode()
            )
    return f'W/"{digest.hexdigest()[:32]}"'


@router.get(
    "/fx",
    response_model=FxResponse,
    summary="Foreign-exchange table: units of each currency per 1 USD",
    responses={
        200: {
            "headers": {
                "ETag": {
                    "description": "Send back as If-None-Match.",
                    "schema": {"type": "string"},
                }
            }
        },
        304: {"description": "Unchanged since the supplied ETag; no body."},
    },
)
def get_fx(
    request: Request,
    response: Response,
    currency: Optional[str] = Query(
        None,
        description="Comma-separated ISO codes, e.g. KZT,EUR",
        examples=["KZT,EUR"],
    ),
    source: Optional[str] = Query(
        None,
        description="Comma-separated source ids",
        examples=["frankfurter"],
    ),
    db: Session = Depends(get_db),
):
    """International rates for converting one **foreign** currency into
    another, with no MNT and no Mongolian bank involved.

    Every rate is how many units of `currency` one US dollar buys.
    USD to X is `rate`; X to USD is `1 / rate`; X to Y (neither USD)
    pivots through USD: `amount / rate_X * rate_Y`, using both rates
    from the **same source** so they come from one snapshot. This
    endpoint only serves the table; conversion is done by the client.
    Refreshed a few times a day, not every 15 minutes.
    """
    source_ids = _parse_currencies(source)
    source_ids = {s.lower() for s in source_ids} if source_ids else None
    base_url = _base_url(request)
    payload = _build_fx(db, _parse_currencies(currency), source_ids, base_url)

    etag = _fx_etag(payload, base_url)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=300"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=dict(response.headers))
    return payload


@router.get(
    "/sources",
    response_model=SourcesResponse,
    summary="Source registry, with the evidence behind each mapping",
)
def get_sources(request: Request):
    """What each source publishes and why we believe it.

    The `evidence` strings record how each channel/side label was
    confirmed, so a future reader can re-check a claim rather than
    trusting it.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": [
            {
                "id": spec.id,
                "name": spec.name,
                "name_mn": spec.name_mn,
                "name_evidence": spec.name_evidence,
                "logo_url": _logo_url(spec.id, _base_url(request)),
                "type": spec.type,
                "cadence": spec.cadence,
                "kind": spec.kind,
                "channels": sorted(spec.channels),
                "evidence": spec.evidence,
            }
            for spec in SPECS
        ],
    }


@router.get(
    "/rates/{source_id}/history",
    response_model=HistoryResponse,
    summary="Snapshot history for one source",
    responses={404: {"description": "Unknown source id."}},
)
def get_history(
    source_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Distinct published rate sets for one source, newest first.

    One entry per actual change - repeated crawls that found the same
    payload do not create entries, they move `last_checked_at`.
    """
    spec = BY_ID.get(source_id.lower())
    if spec is None:
        raise HTTPException(404, f"unknown source: {source_id}")

    rows = db.scalars(
        select(RateSnapshot)
        .where(RateSnapshot.source_id == spec.id)
        .order_by(RateSnapshot.fetched_at.desc(), RateSnapshot.id.desc())
        .limit(limit)
    ).all()

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "id": spec.id,
            "name": spec.name,
            "name_mn": spec.name_mn,
            "type": spec.type,
        },
        "snapshots": [
            {
                "fetched_at": _iso_z(row.fetched_at),
                "published_at": _iso_z(row.published_at),
                "last_checked_at": _iso_z(row.last_checked_at),
                "payload_hash": row.payload_hash,
                "quotes": row.quotes,
            }
            for row in rows
        ],
    }
