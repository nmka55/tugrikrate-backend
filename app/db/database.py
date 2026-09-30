from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import config
from app.db.snapshots import sync_sources
from app.models.snapshot import Base

_is_sqlite = config.DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
# pool_pre_ping: Neon's free Postgres suspends its compute after ~5
# minutes idle and closes every open connection ("terminating
# connection due to administrator command"). Without a liveness check
# the pool hands that dead connection to the next request, which fails
# with a 500 - seen in production on /v1/fx at 15:59 and 16:17 UTC on
# 2026-09-30. Pinging on checkout costs one round-trip and transparently
# replaces a dead connection. See ARCHITECTURE.md §8.
engine = create_engine(
    config.DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create the snapshot schema and seed the source registry.

    No Alembic, matching upstream's convention at this scale: the tables
    are additive and `sync_sources` is idempotent, so startup is safe to
    repeat. `scripts/migrate_v1.py` handles the one-time move off the
    old `currency_rates` table, which is left in place untouched.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sync_sources(db)
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
