"""How often a source is crawled, and when its data counts as stale.

Both the scheduler and the v1 `status` field derive from the same
interval function, so "how often we ask" and "how long before we admit
it is old" can never drift apart.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import config
from app.models.snapshot import RateSnapshot, SourceState
from app.sources.registry import CADENCE_SLOW, SourceSpec

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_FAILING = "failing"


def local_now(at: datetime | None = None) -> datetime:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(ZoneInfo(config.CRAWL_TIMEZONE))


def is_active_hours(at: datetime | None = None) -> bool:
    """True during Mongolian banking hours, when banks actually move
    their rates and the fast cadence is worth paying for."""
    hour = local_now(at).hour
    return (
        config.CRAWL_ACTIVE_START_HOUR <= hour < config.CRAWL_ACTIVE_END_HOUR
    )


def interval_minutes(spec: SourceSpec, at: datetime | None = None) -> int:
    """Expected minutes between crawls of this source right now."""
    base = (
        config.CRAWL_ACTIVE_INTERVAL_MINUTES
        if is_active_hours(at)
        else config.CRAWL_OFFPEAK_INTERVAL_MINUTES
    )
    if spec.cadence == CADENCE_SLOW:
        base *= config.CRAWL_PLAYWRIGHT_MULTIPLIER
    return base


def compute_status(
    spec: SourceSpec,
    snapshot: RateSnapshot | None,
    state: SourceState | None,
    at: datetime | None = None,
) -> str:
    """Classify a source for the v1 feed.

    `failing` means we cannot currently reach it; `stale` means the last
    good data is older than it should be but is still being served.
    A failing source keeps returning its last snapshot - losing the
    rates entirely because a bank had a bad afternoon would be worse
    for the app than showing them with a flag.
    """
    now = at or datetime.now(timezone.utc)
    failures = state.consecutive_failures if state else 0

    if snapshot is None:
        return STATUS_FAILING
    if failures >= config.FAILING_AFTER_ATTEMPTS:
        return STATUS_FAILING

    checked = snapshot.last_checked_at
    if checked is None:
        return STATUS_STALE
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)

    allowance = interval_minutes(spec, now) * config.STALE_AFTER_INTERVALS
    age_minutes = (now - checked).total_seconds() / 60
    if age_minutes > allowance:
        return STATUS_STALE

    # Reaching a source says nothing about whether it has published
    # anything lately. When it states a publication date and that date
    # has gone cold, the data is stale however healthy the crawl was.
    published = snapshot.published_at
    if published is not None:
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        cutoff = timedelta(hours=config.PUBLISHED_STALE_HOURS)
        if now - published > cutoff:
            return STATUS_STALE

    return STATUS_OK
