"""Runs crawls and persists the results.

Replaces upstream's ScraperService. The important behavioural change is
isolation: every source gets its own try/except *and* its own database
session, so one bank timing out, returning garbage, or blowing up mid-
parse cannot affect any other source's result or leave a shared session
in a failed state. A failure never touches the last good snapshot - it
increments a failure counter, and the v1 feed keeps serving the old
rates flagged `stale` or `failing`.

Three more guarantees (2026-10-08), so one bad source can never cost the
others a crawl or lose a good result:

- **Batch deadline.** A run stops waiting after
  CRAWL_BATCH_DEADLINE_SECONDS. Every source that finished is already
  saved (each saves itself the moment it completes). A source still
  running is reported as overdue and keeps going in the background - it
  still saves if it succeeds - but it can no longer hold the scheduler's
  job open (`max_instances=1`), which used to make the *next* slot skip
  every source in the group.
- **One crawl per source at a time.** A source whose previous crawl is
  still running is skipped by the next run rather than started twice
  (two headless Chromiums do not fit the free instance's 512 MB).
- **One persistence retry.** A failed database write (e.g. a Neon
  compute waking from suspend) is retried once on a fresh session, so a
  good crawl is not thrown away for a transient connection error.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import config
from app.db.database import SessionLocal
from app.db.snapshots import record_failure, record_success
from app.sources.adapter import collect, result_hash
from app.sources.registry import CADENCE_SLOW, SPECS, SourceSpec
from app.utils.logger import logger


def target_date() -> str:
    """Today in Mongolia, which is the day the banks are quoting for."""
    return datetime.now(ZoneInfo(config.CRAWL_TIMEZONE)).date().isoformat()


_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def _claim(source_id: str) -> bool:
    with _in_flight_lock:
        if source_id in _in_flight:
            return False
        _in_flight.add(source_id)
        return True


def _release(source_id: str) -> None:
    with _in_flight_lock:
        _in_flight.discard(source_id)


def _persist(spec: SourceSpec, write) -> object:
    """Run `write(db)` on a fresh session, retrying once on a new session
    if the first attempt fails. Raises if both attempts fail."""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        db = SessionLocal()
        try:
            return write(db)
        except Exception as exc:
            last_exc = exc
            try:
                db.rollback()
            except Exception:
                pass
            if attempt == 1:
                logger.warning(
                    f"{spec.id}: database write failed ({exc}); retrying once"
                )
        finally:
            db.close()
    raise last_exc


def crawl_source(spec: SourceSpec, date_str: str | None = None) -> dict:
    """Crawl and persist one source. Never raises."""
    if not _claim(spec.id):
        logger.warning(
            f"{spec.id}: previous crawl still running - skipped this run"
        )
        return {
            "source": spec.id,
            "ok": False,
            "skipped": True,
            "error": "previous crawl still running",
        }
    try:
        return _crawl_and_persist(spec, date_str or target_date())
    finally:
        _release(spec.id)


def _crawl_and_persist(spec: SourceSpec, date_str: str) -> dict:
    try:
        try:
            result = collect(spec, date_str)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            _persist(spec, lambda db: record_failure(db, spec, message))
            return {"source": spec.id, "ok": False, "error": str(exc)}

        if not result.quotes:
            # An empty parse is a failure, not a successful crawl of
            # nothing: publishing it would wipe the source's quotes.
            _persist(
                spec,
                lambda db: record_failure(db, spec, "crawl returned 0 quotes"),
            )
            return {
                "source": spec.id,
                "ok": False,
                "error": "0 quotes parsed",
            }

        digest = result_hash(spec, result)
        _, inserted = _persist(
            spec, lambda db: record_success(db, spec, result, digest)
        )
        return {
            "source": spec.id,
            "ok": True,
            "quotes": len(result.quotes),
            "changed": inserted,
            "warnings": result.warnings,
        }
    except Exception as exc:
        # Persistence failed twice; log loudly but keep the run alive.
        logger.error(f"{spec.id}: could not persist crawl - {exc}")
        return {"source": spec.id, "ok": False, "error": str(exc)}


def crawl_sources(
    specs: tuple[SourceSpec, ...] = SPECS,
    date_str: str | None = None,
    max_workers: int | None = None,
) -> dict:
    """Crawl a set of sources, optionally in parallel."""
    date_str = date_str or target_date()
    if not config.ENABLE_PARALLEL:
        workers = 1
    elif max_workers is not None:
        workers = max_workers
    else:
        # A headless Chromium each is the memory constraint here, so a
        # group containing any Playwright source takes the lower cap.
        workers = (
            config.PLAYWRIGHT_MAX_WORKERS
            if any(spec.cadence == CADENCE_SLOW for spec in specs)
            else config.MAX_WORKERS
        )

    results = []
    deadline = config.CRAWL_BATCH_DEADLINE_SECONDS
    # Always through an executor, even with one worker, so the deadline
    # applies: a sequential loop would let one hung source block the rest.
    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {
        executor.submit(crawl_source, spec, date_str): spec for spec in specs
    }
    done, overdue = wait(futures, timeout=deadline)
    for future in done:
        results.append(future.result())
    for future in overdue:
        spec = futures[future]
        if future.cancel():
            error = f"not started within the {deadline}s batch deadline"
        else:
            error = (
                f"still running after the {deadline}s batch deadline; "
                "it saves its result if it finishes"
            )
        logger.warning(f"{spec.id}: {error}")
        results.append({"source": spec.id, "ok": False, "error": error})
    # Do not wait for overdue crawls; they finish (and save) on their own.
    executor.shutdown(wait=False, cancel_futures=True)

    succeeded = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    changed = [r for r in succeeded if r.get("changed")]

    logger.info(
        f"Crawl finished: {len(succeeded)} ok "
        f"({len(changed)} changed), {len(failed)} failed"
    )
    return {
        "date": date_str,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "changed": len(changed),
        "failed_sources": sorted(r["source"] for r in failed),
        "results": results,
    }
