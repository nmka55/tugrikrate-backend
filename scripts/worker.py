#!/usr/bin/env python3
"""Standalone collector process - no web server.

Runs the crawl scheduler for whichever source group `CRAWL_GROUP` names
and nothing else. The intended split is:

    web service   CRAWL_GROUP=fast   10 JSON sources, serves /v1/rates
    worker        CRAWL_GROUP=slow    5 Playwright sources

which keeps headless Chromium out of the process that answers requests.
That matters because a Chromium instance is the single largest memory
consumer here, and an OOM kill in a combined process takes the API down
with it - upstream hit exactly that repeatedly on a 512MB instance.

The two groups own disjoint sources and share only the database, so
they need no coordination: neither can ever write the other's rows.
Both must point at the same DATABASE_URL, and that has to be Postgres
rather than SQLite once there is more than one process.

Usage:
    CRAWL_GROUP=slow python -m scripts.worker
"""

import signal
import threading

from app.config import config
from app.db.database import init_db
from app.services import scheduler
from app.utils.logger import logger
from app.utils.playwright_setup import ensure_playwright_browsers

_stop = threading.Event()


def _handle_signal(signum, _frame) -> None:
    logger.info(f"Received signal {signum}, shutting down")
    _stop.set()


def main() -> None:
    if not config.SCHEDULER_ENABLED:
        logger.error(
            "SCHEDULER_ENABLED is false, so this worker would do "
            "nothing. Unset it or set it to true."
        )
        raise SystemExit(1)

    if config.CRAWL_GROUP == "slow":
        ensure_playwright_browsers()
    init_db()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    started = scheduler.start()
    if started is None:
        raise SystemExit(1)

    logger.info(
        f"Worker running for group '{config.CRAWL_GROUP}'. "
        "Waiting for scheduled crawls."
    )
    try:
        _stop.wait()
    finally:
        scheduler.shutdown()
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
