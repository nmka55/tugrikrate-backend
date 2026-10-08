"""The collector keeps every other source's crawl and save intact when one
source fails, hangs, or hits a transient database error."""

import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import config
from app.models.snapshot import RateSnapshot, SourceState
from app.services import collector
from app.sources.models import CrawlResult, Quote
from app.sources.registry import BY_ID

KHAN, GOLOMT, XAC = BY_ID["khanbank"], BY_ID["golomtbank"], BY_ID["xacbank"]


def ok_result(spec, rate="3586"):
    return CrawlResult(
        source_id=spec.id,
        quotes=[Quote("USD", "cash", "buy", Decimal(rate))],
        payload=f'{{"usd":{rate}}}'.encode(),
    )


@pytest.fixture
def sessions(test_db, monkeypatch):
    """Point the collector's SessionLocal at the in-memory test database."""
    factory = sessionmaker(bind=test_db.get_bind())
    monkeypatch.setattr(collector, "SessionLocal", factory)
    return factory


def snapshot_ids(db):
    db.expire_all()
    return sorted(s.source_id for s in db.query(RateSnapshot).all())


class TestOneFailureDoesNotStopOthers:
    def test_failed_and_empty_sources_do_not_block_saves(
        self, sessions, test_db, monkeypatch
    ):
        def fake_collect(spec, date_str):
            if spec is GOLOMT:
                raise ConnectionError("bank down")
            if spec is XAC:
                return CrawlResult(source_id=spec.id, quotes=[], payload=b"")
            return ok_result(spec)

        monkeypatch.setattr(collector, "collect", fake_collect)
        summary = collector.crawl_sources((KHAN, GOLOMT, XAC), max_workers=2)

        assert summary["succeeded"] == 1
        assert summary["failed_sources"] == ["golomtbank", "xacbank"]
        assert snapshot_ids(test_db) == ["khanbank"]
        states = {s.source_id: s for s in test_db.query(SourceState).all()}
        assert states["golomtbank"].consecutive_failures == 1
        assert "bank down" in states["golomtbank"].last_error
        assert states["xacbank"].last_error == "crawl returned 0 quotes"


class TestHungSource:
    def test_deadline_returns_and_keeps_finished_results(
        self, sessions, test_db, monkeypatch
    ):
        release = threading.Event()

        def fake_collect(spec, date_str):
            if spec is GOLOMT:
                release.wait(5)
            return ok_result(spec)

        monkeypatch.setattr(collector, "collect", fake_collect)
        monkeypatch.setattr(config, "CRAWL_BATCH_DEADLINE_SECONDS", 1)

        started = time.monotonic()
        summary = collector.crawl_sources((KHAN, GOLOMT), max_workers=2)
        assert time.monotonic() - started < 4

        assert summary["failed_sources"] == ["golomtbank"]
        golomt = next(
            r for r in summary["results"] if r["source"] == "golomtbank"
        )
        assert "batch deadline" in golomt["error"]
        assert snapshot_ids(test_db) == ["khanbank"]

        # The overdue crawl still saves once it finishes.
        release.set()
        for _ in range(50):
            if "golomtbank" in snapshot_ids(test_db):
                break
            time.sleep(0.1)
        assert snapshot_ids(test_db) == ["golomtbank", "khanbank"]

    def test_a_still_running_source_is_skipped_not_doubled(
        self, sessions, monkeypatch
    ):
        release = threading.Event()
        calls = []

        def fake_collect(spec, date_str):
            calls.append(spec.id)
            release.wait(5)
            return ok_result(spec)

        monkeypatch.setattr(collector, "collect", fake_collect)
        first = threading.Thread(target=collector.crawl_source, args=(KHAN,))
        first.start()
        for _ in range(50):
            if calls:
                break
            time.sleep(0.02)

        second = collector.crawl_source(KHAN)
        release.set()
        first.join(5)

        assert second["skipped"] is True
        assert calls == ["khanbank"]
        # Released afterwards: the next run crawls it again.
        assert collector.crawl_source(KHAN)["ok"] is True


class TestPersistenceRetry:
    def test_a_transient_write_failure_is_retried(
        self, sessions, test_db, monkeypatch
    ):
        monkeypatch.setattr(
            collector, "collect", lambda spec, d: ok_result(spec)
        )
        real = collector.record_success
        attempts = []

        def flaky(db, spec, result, digest):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError(
                    "SSL connection has been closed unexpectedly"
                )
            return real(db, spec, result, digest)

        monkeypatch.setattr(collector, "record_success", flaky)
        assert collector.crawl_source(KHAN)["ok"] is True
        assert len(attempts) == 2
        assert snapshot_ids(test_db) == ["khanbank"]

    def test_two_write_failures_are_reported_not_raised(
        self, sessions, monkeypatch
    ):
        monkeypatch.setattr(
            collector, "collect", lambda spec, d: ok_result(spec)
        )

        def broken(*args):
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(collector, "record_success", broken)
        result = collector.crawl_source(KHAN)
        assert result["ok"] is False
        assert "database unreachable" in result["error"]
