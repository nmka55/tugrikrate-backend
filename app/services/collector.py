"""Runs crawls and persists the results.

Replaces upstream's ScraperService. The important behavioural change is
isolation: every source gets its own try/except *and* its own database
session, so one bank timing out, returning garbage, or blowing up mid-
parse cannot affect any other source's result or leave a shared session
in a failed state. A failure never touches the last good snapshot - it
increments a failure counter, and the v1 feed keeps serving the old
rates flagged `stale` or `failing`.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
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


def crawl_source(spec: SourceSpec, date_str: str | None = None) -> dict:
    """Crawl and persist one source. Never raises."""
    date_str = date_str or target_date()
    db = SessionLocal()
    try:
        try:
            result = collect(spec, date_str)
        except Exception as exc:
            record_failure(db, spec, f"{type(exc).__name__}: {exc}")
            return {"source": spec.id, "ok": False, "error": str(exc)}

        if not result.quotes:
            # An empty parse is a failure, not a successful crawl of
            # nothing: publishing it would wipe the source's quotes.
            record_failure(db, spec, "crawl returned 0 quotes")
            return {
                "source": spec.id,
                "ok": False,
                "error": "0 quotes parsed",
            }

        digest = result_hash(spec, result)
        _, inserted = record_success(db, spec, result, digest)
        return {
            "source": spec.id,
            "ok": True,
            "quotes": len(result.quotes),
            "changed": inserted,
            "warnings": result.warnings,
        }
    except Exception as exc:
        # Persistence itself failed; log loudly but keep the run alive.
        logger.error(f"{spec.id}: could not persist crawl - {exc}")
        return {"source": spec.id, "ok": False, "error": str(exc)}
    finally:
        db.close()


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
    if workers > 1 and len(specs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(crawl_source, spec, date_str) for spec in specs
            ]
            for future in as_completed(futures):
                results.append(future.result())
    else:
        for spec in specs:
            results.append(crawl_source(spec, date_str))

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
