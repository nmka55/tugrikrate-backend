"""Crawl scheduling.

Replaces upstream's `schedule`-library daily 09:00 job, which had no
timezone handling and no way to vary cadence per source.

Cadence, all in Asia/Ulaanbaatar:

    08:00-20:00   every 15 min   (CRAWL_ACTIVE_INTERVAL_MINUTES)
    otherwise     hourly         (CRAWL_OFFPEAK_INTERVAL_MINUTES)

The five Playwright sources cost a headless Chromium per crawl, so they
run on a multiple of that (CRAWL_PLAYWRIGHT_MULTIPLIER, default 4 =>
hourly while active). Every trigger carries random jitter so a bank
never sees this service arrive on an exact interval boundary.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import config
from app.services.collector import crawl_sources
from app.sources.registry import (
    CADENCE_DAILY,
    CADENCE_FAST,
    CADENCE_SLOW,
    specs_for_group,
)
from app.utils.call_budget import planned_daily_calls
from app.utils.logger import logger

_scheduler: BackgroundScheduler | None = None

# Frankfurter lists 166 currencies; MNT and the four metals are not
# requested. Deliberately a little high so growth in its catalogue does
# not silently break the budget check.
_INTL_CURRENCY_ESTIMATE = 170


def _active_hours() -> list[int]:
    start, end = config.CRAWL_ACTIVE_START_HOUR, config.CRAWL_ACTIVE_END_HOUR
    return [hour % 24 for hour in range(start, end)]


def _offpeak_hours() -> list[int]:
    active = set(_active_hours())
    return [hour for hour in range(24) if hour not in active]


def _cron_fields(interval: int, hours: list[int]) -> dict | None:
    """Express "every `interval` minutes, within `hours`" as cron.

    Returns None when the window has no slots at this interval, so the
    caller can skip registering an unfireable job.
    """
    if not hours:
        return None
    if interval < 60:
        step = interval if 60 % interval == 0 else 60
        return {
            "minute": f"*/{step}",
            "hour": ",".join(str(h) for h in sorted(hours)),
        }
    step_hours = max(1, interval // 60)
    chosen = [h for h in sorted(hours) if h % step_hours == 0]
    if not chosen:
        chosen = [min(hours)]
    return {"minute": "0", "hour": ",".join(str(h) for h in chosen)}


def _register(
    scheduler: BackgroundScheduler,
    job_id: str,
    specs: tuple,
    interval: int,
    hours: list[int],
) -> None:
    fields = _cron_fields(interval, hours)
    if fields is None or not specs:
        return
    trigger = CronTrigger(
        timezone=config.CRAWL_TIMEZONE,
        jitter=config.CRAWL_JITTER_SECONDS or None,
        **fields,
    )
    scheduler.add_job(
        crawl_sources,
        trigger=trigger,
        id=job_id,
        args=[specs],
        # A crawl that overruns its slot must not stack on itself, and
        # a missed slot should be dropped rather than replayed - the
        # next one is only minutes away and carries fresher rates.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )
    logger.info(
        f"Scheduled {job_id}: {len(specs)} source(s), "
        f"every {interval}m at hours {fields['hour']} "
        f"({config.CRAWL_TIMEZONE}, +/-{config.CRAWL_JITTER_SECONDS}s jitter)"
    )


def build_scheduler(group: str | None = None) -> BackgroundScheduler:
    """Build the scheduler for one source group.

    A process only ever schedules the sources it owns, so running a
    `fast` web service alongside a `slow` worker collects every source
    exactly once with no coordination between them.
    """
    group = group or config.CRAWL_GROUP
    owned = specs_for_group(group)
    http = tuple(s for s in owned if s.cadence == CADENCE_FAST)
    browser = tuple(s for s in owned if s.cadence == CADENCE_SLOW)
    daily = tuple(s for s in owned if s.cadence == CADENCE_DAILY)

    scheduler = BackgroundScheduler(timezone=config.CRAWL_TIMEZONE)
    active, offpeak = _active_hours(), _offpeak_hours()
    fast = config.CRAWL_ACTIVE_INTERVAL_MINUTES
    slow_mult = config.CRAWL_PLAYWRIGHT_MULTIPLIER
    offpeak_interval = config.CRAWL_OFFPEAK_INTERVAL_MINUTES

    _register(scheduler, "http-active", http, fast, active)
    _register(scheduler, "http-offpeak", http, offpeak_interval, offpeak)
    _register(scheduler, "browser-active", browser, fast * slow_mult, active)
    _register(
        scheduler,
        "browser-offpeak",
        browser,
        offpeak_interval * slow_mult,
        offpeak,
    )
    if daily:
        _check_call_budget()
        # One job around the clock: these publish once a day, so the
        # banking-hours split has no meaning for them.
        _register(
            scheduler,
            "intl-daily",
            daily,
            config.INTL_CRAWL_INTERVAL_HOURS * 60,
            list(range(24)),
        )
    return scheduler


def _check_call_budget() -> None:
    """Refuse to start a schedule that needs more requests per day than
    the configured ceiling allows. Failing at startup is loud; letting
    the runtime guard silently starve the last crawls of the day is not.
    """
    # Imported here: the crawler module builds its budget from config
    # at import time, which the scheduler tests must not depend on.
    from app.crawlers.frankfurter import BUDGET

    # +1 for the currency catalogue request that starts every crawl.
    calls_per_crawl = _INTL_CURRENCY_ESTIMATE + 1
    planned = planned_daily_calls(
        config.INTL_CRAWL_INTERVAL_HOURS, calls_per_crawl
    )
    if planned > BUDGET.limit:
        raise ValueError(
            f"INTL_CRAWL_INTERVAL_HOURS={config.INTL_CRAWL_INTERVAL_HOURS} "
            f"plans ~{planned} calls/day but INTL_DAILY_CALL_LIMIT="
            f"{BUDGET.limit}; lengthen the interval or raise the limit"
        )
    logger.info(
        f"International call budget: ~{planned} planned of "
        f"{BUDGET.limit} calls/day"
    )


def start() -> BackgroundScheduler | None:
    global _scheduler
    if not config.SCHEDULER_ENABLED:
        logger.info(
            "Scheduler disabled (SCHEDULER_ENABLED=false) - crawls must "
            "be triggered via POST /api/admin/crawl"
        )
        return None
    if _scheduler is not None:
        return _scheduler
    logger.info(f"Collecting source group: {config.CRAWL_GROUP}")
    _scheduler = build_scheduler()
    _scheduler.start()
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
