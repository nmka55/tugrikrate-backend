"""SNAPSHOT_RETENTION_DAYS: the setting was documented but did nothing
until 2026-11-15. These pin that it is wired to a real job, off by
default, and owned by exactly one process."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import config
from app.db import snapshots as repo
from app.models.snapshot import Base, RateSnapshot
from app.services import retention
from app.services.scheduler import PRUNE_HOUR, PRUNE_MINUTE, build_scheduler
from app.sources.models import CrawlResult, Quote
from app.sources.registry import BY_ID

SPEC = BY_ID["khanbank"]
JOB_ID = "prune-snapshots"


def job_ids(group):
    return {job.id for job in build_scheduler(group).get_jobs()}


class TestRetentionSchedule:
    def test_off_by_default(self):
        assert config.SNAPSHOT_RETENTION_DAYS == 0
        assert JOB_ID not in job_ids("all")

    def test_scheduled_once_a_day_when_set(self, monkeypatch):
        monkeypatch.setattr(config, "SNAPSHOT_RETENTION_DAYS", 90)
        jobs = {j.id: j for j in build_scheduler("all").get_jobs()}
        job = jobs[JOB_ID]
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == str(PRUNE_HOUR)
        assert fields["minute"] == str(PRUNE_MINUTE)
        assert str(job.trigger.timezone) == config.CRAWL_TIMEZONE
        assert job.max_instances == 1

    def test_only_one_process_of_a_split_prunes(self, monkeypatch):
        """The prune touches every source's rows, so it must not run in
        both halves of a fast/slow split."""
        monkeypatch.setattr(config, "SNAPSHOT_RETENTION_DAYS", 90)
        assert JOB_ID in job_ids("fast")
        assert JOB_ID not in job_ids("slow")


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(retention, "SessionLocal", factory)
    yield factory
    engine.dispose()


def seed(db, rate, age_days):
    snapshot, _ = repo.record_success(
        db,
        SPEC,
        CrawlResult(
            source_id=SPEC.id,
            quotes=[Quote("USD", "cash", "buy", Decimal(rate))],
        ),
        "h",
    )
    snapshot.fetched_at = snapshot.fetched_at - timedelta(days=age_days)
    db.commit()


class TestPruneJob:
    def test_applies_the_configured_retention(self, sessions, monkeypatch):
        monkeypatch.setattr(config, "SNAPSHOT_RETENTION_DAYS", 30)
        with sessions() as db:
            seed(db, "1.00", age_days=40)
            seed(db, "2.00", age_days=0)

        assert retention.prune_job() == 1
        with sessions() as db:
            assert [r.quotes[0]["rate"] for r in db.query(RateSnapshot)] == [
                "2.00"
            ]

    def test_a_database_error_is_reported_not_raised(
        self, sessions, monkeypatch
    ):
        monkeypatch.setattr(config, "SNAPSHOT_RETENTION_DAYS", 30)

        def explode(db, keep_days):
            raise RuntimeError("connection lost")

        monkeypatch.setattr(retention, "prune_snapshots", explode)
        assert retention.prune_job() == 0
