"""How often a source is crawled, and when its data counts as stale.

Both the scheduler and the v1 `status` field derive from the same
interval function, so "how often we ask" and "how long before we admit
it is old" can never drift apart.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import config
from app.models.snapshot import RateSnapshot, SourceState
from app.sources.registry import CADENCE_DAILY, CADENCE_SLOW, SourceSpec

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_FAILING = "failing"


def local_now(at: datetime | None = None) -> datetime:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(ZoneInfo(config.CRAWL_TIMEZONE))


def slots_for(spec: SourceSpec) -> list[int]:
    """Minutes-of-day (local) at which this source is crawled.

    Working hours run from CRAWL_ACTIVE_START_HOUR to the end hour at the
    active step; the stretch from close back round to the next opening
    is filled at the off-peak step, so a 6 h step after an 18:00 close
    gives 00:00 and 06:00 before 09:00 resumes. JSON and Playwright
    banks have their own steps.
    """
    if spec.cadence == CADENCE_SLOW:
        active = config.CRAWL_BROWSER_ACTIVE_MINUTES
        offpeak = config.CRAWL_BROWSER_OFFPEAK_MINUTES
    else:
        active = config.CRAWL_JSON_ACTIVE_MINUTES
        offpeak = config.CRAWL_JSON_OFFPEAK_MINUTES
    start = config.CRAWL_ACTIVE_START_HOUR * 60
    end = config.CRAWL_ACTIVE_END_HOUR * 60
    slots = set(range(start, end + 1, active))
    slots.update(t % 1440 for t in range(end, start + 1440, offpeak))
    return sorted(slots)


def interval_minutes(spec: SourceSpec, at: datetime | None = None) -> int:
    """Expected minutes between crawls of this source right now.

    For a bank this is the gap between the two slots (`slots_for`) that
    surround `at`, wrapping past midnight, so a long overnight gap is
    not mistaken for an outage and short daytime ones are held to a
    tighter allowance.
    """
    if spec.cadence == CADENCE_DAILY:
        # Not tied to the bank slots: it publishes a daily table.
        return 24 * 60 // config.INTL_CRAWLS_PER_DAY
    local = local_now(at)
    now = local.hour * 60 + local.minute
    slots = slots_for(spec)
    previous = max((t for t in slots if t <= now), default=slots[-1] - 1440)
    following = min((t for t in slots if t > now), default=slots[0] + 1440)
    return following - previous


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
        cutoff = timedelta(
            hours=spec.published_stale_hours or config.PUBLISHED_STALE_HOURS
        )
        if now - published > cutoff:
            return STATUS_STALE

    return STATUS_OK
