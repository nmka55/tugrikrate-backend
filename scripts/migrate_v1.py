#!/usr/bin/env python3
"""One-time move onto the v1 snapshot schema.

Creates `sources`, `rate_snapshots` and `source_state`, and seeds the
source registry. The old `currency_rates` table is left exactly where it
is - nothing is dropped, converted or rewritten.

Its rows are deliberately not migrated. They hold one overwritten row
per (bank, date) whose channel labels are the ones this rebuild exists
to correct: Capitron's non-cash rate stored as its cash rate, TransBank's
sides reversed, the Bank of Mongolia reference duplicated into a fake
buy/sell spread, and three sources' cash values copied into non-cash.
Importing them would put known-wrong data behind real timestamps. Drop
the table by hand once you are satisfied nothing needs it:

    DROP TABLE currency_rates;

Usage:
    python -m scripts.migrate_v1
"""

from sqlalchemy import inspect, text

from app.db.database import engine, init_db
from app.sources.registry import SPECS
from app.utils.logger import logger

LEGACY_TABLE = "currency_rates"
NEW_TABLES = ("sources", "rate_snapshots", "source_state")


def main() -> None:
    inspector = inspect(engine)
    before = set(inspector.get_table_names())

    logger.info("Creating v1 snapshot schema...")
    init_db()

    inspector = inspect(engine)
    after = set(inspector.get_table_names())

    for table in NEW_TABLES:
        state = "created" if table not in before else "already present"
        logger.info(f"  {table}: {state}")

    if LEGACY_TABLE in after:
        with engine.connect() as conn:
            count = conn.execute(
                text(f"SELECT COUNT(*) FROM {LEGACY_TABLE}")
            ).scalar_one()
        logger.warning(
            f"Legacy table '{LEGACY_TABLE}' still holds {count} row(s). "
            "It is untouched and unused; see this module's docstring "
            "for why those rows are not imported."
        )
    else:
        logger.info(f"No legacy '{LEGACY_TABLE}' table present.")

    logger.info(f"Seeded {len(SPECS)} sources. Migration complete.")


if __name__ == "__main__":
    main()
