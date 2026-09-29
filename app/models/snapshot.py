"""Snapshot schema.

Replaces upstream's single `currency_rates` table, which held one row
per (bank, date) and overwrote it on every crawl - so nothing above a
daily resolution survived, and a bank going quiet was indistinguishable
from a bank republishing the same numbers.

Rates are stored as decimal *strings* inside the `quotes` JSON, not as
floats and not as NUMERIC. SQLite has no exact numeric type - SQLAlchemy
routes Numeric through float there and warns about it - so a string is
the only representation that round-trips identically on both SQLite and
Postgres. The service passes these straight into the JSON response, so
no conversion ever happens.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetimes that survive SQLite.

    SQLite has no native timestamp type, so SQLAlchemy stores a naive
    string and reads back a naive datetime - silently turning a UTC
    instant into an ambiguous one. This normalises to UTC on the way in
    and re-attaches UTC on the way out, so callers always hold an aware
    datetime regardless of dialect.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Source(Base):
    """The registry, mirrored into the database so snapshots keep
    meaning if a source is later retired from the code."""

    __tablename__ = "sources"

    id = Column(String(32), primary_key=True)
    name = Column(String(128), nullable=False)
    name_mn = Column(String(128))
    type = Column(String(32), nullable=False)
    cadence = Column(String(16), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)


class RateSnapshot(Base):
    """One distinct set of published rates from one source.

    A row is inserted only when `payload_hash` differs from the latest
    row for that source. An unchanged crawl bumps `last_checked_at` on
    the existing row instead, so the table grows with real rate changes
    rather than with crawl frequency.
    """

    __tablename__ = "rate_snapshots"
    __table_args__ = (
        Index("ix_rate_snapshots_source_fetched", "source_id", "fetched_at"),
    )

    id = Column(Integer, primary_key=True)
    source_id = Column(String(32), nullable=False, index=True)

    # When this service retrieved the rates.
    fetched_at = Column(UTCDateTime, nullable=False, default=utc_now)
    # When the *source* says it published them. Null whenever the
    # source does not say - never back-filled from fetched_at.
    published_at = Column(UTCDateTime, nullable=True)
    # Most recent crawl that produced this same payload.
    last_checked_at = Column(UTCDateTime, nullable=False, default=utc_now)

    payload_hash = Column(String(64), nullable=False, index=True)
    quotes = Column(JSON, nullable=False)


class SourceState(Base):
    """Failure streak and last-attempt bookkeeping behind `status`.

    Kept off `rate_snapshots` deliberately: a failing crawl must not
    disturb the last good snapshot, which stays served and flagged
    stale.
    """

    __tablename__ = "source_state"

    source_id = Column(String(32), primary_key=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(UTCDateTime)
    last_success_at = Column(UTCDateTime)
    last_error = Column(Text)
    last_error_at = Column(UTCDateTime)
