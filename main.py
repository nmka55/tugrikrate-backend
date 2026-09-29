"""One-shot crawl of every source, then exit.

Useful for seeding a fresh database before starting the API, or for
running the collector from an external scheduler.
"""

from app.db.database import init_db
from app.services.collector import crawl_sources
from app.utils.playwright_setup import ensure_playwright_browsers


def main():
    ensure_playwright_browsers()
    init_db()
    crawl_sources()


if __name__ == "__main__":
    main()
