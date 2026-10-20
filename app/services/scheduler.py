"""Crawl scheduling.

Replaces upstream's `schedule`-library daily 09:00 job, which had no
timezone handling and no way to vary cadence per source.

Banks are crawled on slots from freshness.slots_for, in
Asia/Ulaanbaatar. Defaults: JSON banks every 45 min over 09:00-18:00
and every 6 h after close; Playwright banks every 3 h over working
hours and every 5 h overnight. Every trigger carries random jitter so
a bank never sees this service arrive on an exact boundary.

International sources (Frankfurter, fxRatesAPI) run INTL_CRAWLS_PER_DAY
times a day around the clock. One whose API key is not configured is
not scheduled at all.
"""

import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import config
from app.services.collector import crawl_sources
from app.services.freshness import slots_for
from app.sources.registry import (
    CADENCE_DAILY,
    CADENCE_FAST,
    CADENCE_SLOW,
    specs_for_group,
)
from app.utils.logger import logger

_scheduler: BackgroundScheduler | None = None


def _cron_fields(interval: int, hours) -> dict | None:
    """Express "every `interval` minutes, within `hours`" as cron.

    Returns None when the window has no slots at this interval, so the
    caller can skip registering an unfireable job.
    """
    if not hours:
        return None
    step_hours = max(1, interval // 60)
    chosen = [h for h in sorted(hours) if h % step_hours == 0]
    if not chosen:
        chosen = [min(hours)]
    return {"minute": "0", "hour": ",".join(str(h) for h in chosen)}


def _register(
    scheduler: BackgroundScheduler,
    job_id: str,
    specs: tuple,
    fields: dict | None,
) -> None:
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
        # A crawl that overruns its slot must not stack on itself. A
        # slot missed by more than 15 minutes (a deploy restart) is
        # dropped rather than replayed: the data would be stale anyway.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
        replace_existing=True,
    )
    logger.info(
        f"Scheduled {job_id}: {len(specs)} source(s), "
        f"at {fields['hour']}:{fields['minute']} "
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
    for spec in daily:
        if not spec.configured:
            logger.info(
                f"{spec.id}: not scheduled - {spec.requires_config} is not set"
            )
    daily = tuple(s for s in daily if s.configured)

    scheduler = BackgroundScheduler(timezone=config.CRAWL_TIMEZONE)

    for kind, specs in (("http", http), ("browser", browser)):
        if not specs:
            continue
        for slot in slots_for(specs[0]):
            hour, minute = divmod(slot, 60)
            fields = {"hour": str(hour), "minute": str(minute)}
            _register(
                scheduler, f"{kind}-{hour:02d}{minute:02d}", specs, fields
            )
    if daily:
        _check_call_budget(daily)
        # One job around the clock: these publish once a day, so the
        # banking-hours split has no meaning for them.
        _register(
            scheduler,
            "intl-daily",
            daily,
            _cron_fields(24 * 60 // config.INTL_CRAWLS_PER_DAY, range(24)),
        )
    return scheduler


def _check_call_budget(specs: tuple) -> None:
    """Refuse to start a schedule that needs more requests per day than
    the configured ceiling allows. Failing at startup is loud; letting
    the runtime guard silently starve the last fetches of the day is not.

    Every international source keeps its own budget (a module-level
    BUDGET in its crawler module), so each is checked separately.
    """
    # One request per fetch: the whole table is a single response.
    planned = config.INTL_CRAWLS_PER_DAY
    for spec in specs:
        # Read at call time: tests replace BUDGET on the module.
        budget = sys.modules[spec.crawler.__module__].BUDGET
        if planned > budget.limit:
            raise ValueError(
                f"{spec.id}: INTL_CRAWLS_PER_DAY="
                f"{config.INTL_CRAWLS_PER_DAY} plans {planned} calls/day "
                f"but INTL_DAILY_CALL_LIMIT={budget.limit}; lower the "
                "fetch rate or raise the limit"
            )
        logger.info(
            f"{spec.id}: call budget {planned} planned of "
            f"{budget.limit} calls/day"
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
