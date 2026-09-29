"""Snapshot persistence: insert on change, bump on no-change."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.snapshot import RateSnapshot, Source, SourceState, utc_now
from app.sources.models import CrawlResult
from app.sources.registry import SPECS, SourceSpec
from app.utils.decimals import format_decimal
from app.utils.logger import logger


def serialize_quotes(result: CrawlResult) -> list[dict]:
    """Quotes as they will appear in the API response.

    Rates are rendered to strings here, once, at the storage boundary -
    so what is stored is byte-for-byte what is served and there is no
    later conversion step that could reintroduce a float.
    """
    return [
        {
            "currency": quote.currency,
            "channel": quote.channel,
            "side": quote.side,
            "rate": format_decimal(quote.rate),
            "unit_basis": format_decimal(quote.unit_basis),
            "verified": quote.verified,
        }
        for quote in sorted(result.quotes, key=lambda q: q.key)
    ]


def sync_sources(db: Session) -> None:
    """Mirror the registry into the `sources` table (idempotent)."""
    existing = {row.id: row for row in db.scalars(select(Source))}
    for spec in SPECS:
        row = existing.get(spec.id)
        if row is None:
            db.add(
                Source(
                    id=spec.id,
                    name=spec.name,
                    name_mn=spec.name_mn,
                    type=spec.type,
                    cadence=spec.cadence,
                    enabled=True,
                )
            )
        else:
            row.name = spec.name
            row.name_mn = spec.name_mn
            row.type = spec.type
            row.cadence = spec.cadence
    db.commit()


def latest_snapshot(db: Session, source_id: str) -> RateSnapshot | None:
    return db.scalars(
        select(RateSnapshot)
        .where(RateSnapshot.source_id == source_id)
        .order_by(RateSnapshot.fetched_at.desc(), RateSnapshot.id.desc())
        .limit(1)
    ).first()


def latest_snapshots(db: Session) -> dict[str, RateSnapshot]:
    """Most recent snapshot per source, in one pass."""
    newest: dict[str, RateSnapshot] = {}
    for row in db.scalars(
        select(RateSnapshot).order_by(
            RateSnapshot.fetched_at.asc(), RateSnapshot.id.asc()
        )
    ):
        newest[row.source_id] = row
    return newest


def source_states(db: Session) -> dict[str, SourceState]:
    return {row.source_id: row for row in db.scalars(select(SourceState))}


def _state_for(db: Session, source_id: str) -> SourceState:
    state = db.get(SourceState, source_id)
    if state is None:
        state = SourceState(source_id=source_id, consecutive_failures=0)
        db.add(state)
    return state


def record_success(
    db: Session,
    spec: SourceSpec,
    result: CrawlResult,
    payload_hash: str,
) -> tuple[RateSnapshot, bool]:
    """Persist a successful crawl.

    Returns (snapshot, inserted). `inserted` is False when the payload
    was unchanged and only `last_checked_at` moved.
    """
    now = utc_now()
    state = _state_for(db, spec.id)
    state.consecutive_failures = 0
    state.last_attempt_at = now
    state.last_success_at = now
    state.last_error = None

    previous = latest_snapshot(db, spec.id)
    if previous is not None and previous.payload_hash == payload_hash:
        previous.last_checked_at = now
        # A source can restate the same rates under a newer publication
        # date; that is worth recording without opening a new snapshot.
        if (
            result.published_at
            and previous.published_at != result.published_at
        ):
            previous.published_at = result.published_at
        db.commit()
        return previous, False

    snapshot = RateSnapshot(
        source_id=spec.id,
        fetched_at=now,
        published_at=result.published_at,
        last_checked_at=now,
        payload_hash=payload_hash,
        quotes=serialize_quotes(result),
    )
    db.add(snapshot)
    db.commit()
    logger.info(
        f"{spec.id}: new snapshot ({len(result.quotes)} quotes, "
        f"hash {payload_hash[:12]})"
    )
    return snapshot, True


def record_failure(db: Session, spec: SourceSpec, error: str) -> None:
    """Record a failed crawl without touching the last good snapshot."""
    now = utc_now()
    state = _state_for(db, spec.id)
    state.consecutive_failures = (state.consecutive_failures or 0) + 1
    state.last_attempt_at = now
    state.last_error = error[:2000]
    state.last_error_at = now
    db.commit()
    logger.error(
        f"{spec.id}: crawl failed "
        f"({state.consecutive_failures} in a row) - {error}"
    )


def prune_snapshots(db: Session, keep_days: int) -> int:
    """Drop snapshots older than `keep_days`, always keeping the most
    recent one per source so a quiet source never vanishes."""
    if keep_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    keep_ids = {
        snapshot.id
        for snapshot in latest_snapshots(db).values()
        if snapshot is not None
    }
    removed = 0
    for row in db.scalars(
        select(RateSnapshot).where(RateSnapshot.fetched_at < cutoff)
    ):
        if row.id not in keep_ids:
            db.delete(row)
            removed += 1
    if removed:
        db.commit()
        logger.info(f"Pruned {removed} snapshot(s) older than {keep_days}d")
    return removed
