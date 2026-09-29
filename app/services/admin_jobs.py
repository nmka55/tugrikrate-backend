"""In-process job lock and state for the HTTP-triggered admin crawl.

Kept from upstream because it is still the escape hatch when the
in-process scheduler is turned off (SCHEDULER_ENABLED=false) and an
external trigger drives crawls over HTTP instead.

The lock is correct only while Uvicorn runs as a single process (no
--workers flag) - true for this deployment. Backfill is gone: under
snapshot semantics there is nothing to backfill, because banks do not
serve historical intraday rates and writing invented ones would put
fabricated history behind a real timestamp.
"""

import threading
from datetime import datetime, timezone
from typing import Optional

from app.config import config
from app.services.collector import crawl_source, crawl_sources
from app.sources.registry import BY_ID, specs_for_group
from app.utils.logger import logger

_lock = threading.Lock()
_state = {
    "is_running": False,
    "job_type": None,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
    "last_error": None,
}


def get_status() -> dict:
    return dict(_state)


def try_start(job_type: str) -> bool:
    """Non-blocking lock acquire. The caller must call finish() exactly
    once afterwards, whether the job runs sync or in the background."""
    if not _lock.acquire(blocking=False):
        return False
    _state.update(
        is_running=True,
        job_type=job_type,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
    )
    return True


def finish(result: Optional[dict] = None, error: Optional[str] = None) -> None:
    _state.update(
        is_running=False,
        finished_at=datetime.now(timezone.utc).isoformat(),
        last_result=result,
        last_error=error,
    )
    _lock.release()


def run_crawl_job() -> None:
    """Background task for POST /api/admin/crawl. Must be a plain `def`
    so FastAPI runs it in a worker thread and Uvicorn's event loop stays
    free while this blocks on I/O."""
    try:
        # Scoped to this process's own group, so triggering a crawl on
        # the web service cannot race a worker that owns other sources.
        finish(result=crawl_sources(specs_for_group(config.CRAWL_GROUP)))
    except Exception as exc:
        logger.error(f"Admin crawl job failed: {exc}")
        finish(error=str(exc))


def run_single_source_job(source_id: str) -> dict:
    """Synchronous single-source crawl. crawl_source persists its own
    result and never raises, so the outcome is always reportable."""
    try:
        result = crawl_source(BY_ID[source_id])
        finish(result=result)
        return result
    except Exception as exc:
        logger.error(f"Admin crawl failed for {source_id}: {exc}")
        finish(error=str(exc))
        raise
