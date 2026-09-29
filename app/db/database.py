from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import config
from app.db.snapshots import sync_sources
from app.models.snapshot import Base

_is_sqlite = config.DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args)
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
