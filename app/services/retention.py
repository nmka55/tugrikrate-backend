"""Snapshot retention: the scheduled job behind SNAPSHOT_RETENTION_DAYS.

Until 2026-11-15 the setting was documented but never read, and
`prune_snapshots` had no caller, so the table could only grow. The
scheduler now runs this once a day when the setting is above zero. The
default stays 0 (keep everything): deleting history is the owner's
decision, not a default.
"""

from app.config import config
from app.db.database import SessionLocal
from app.db.snapshots import prune_snapshots
from app.utils.logger import logger


def prune_job() -> int:
    """Drop snapshots past SNAPSHOT_RETENTION_DAYS. Never raises: a
    failed prune is simply retried by the next day's run."""
    db = SessionLocal()
    try:
        return prune_snapshots(db, config.SNAPSHOT_RETENTION_DAYS)
    except Exception as exc:
        db.rollback()
        logger.error(f"Snapshot prune failed: {exc}")
        return 0
    finally:
        db.close()
