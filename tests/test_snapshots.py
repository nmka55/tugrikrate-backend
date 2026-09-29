"""Snapshot persistence: insert on change, bump on no-change, and keep
a failing source's last good data intact."""

from decimal import Decimal

from app.db import snapshots as repo
from app.models.snapshot import RateSnapshot
from app.sources.models import CrawlResult, Quote
from app.sources.registry import BY_ID

SPEC = BY_ID["khanbank"]


def result(rate="3450.50", **kwargs):
    return CrawlResult(
        source_id=SPEC.id,
        quotes=[
            Quote("USD", "cash", "buy", Decimal(rate)),
            Quote("USD", "cash", "sell", Decimal("3480.00")),
        ],
        payload=f'{{"usd":{rate}}}'.encode(),
        **kwargs,
    )


class TestInsertOnChange:
    def test_first_crawl_inserts(self, test_db):
        snapshot, inserted = repo.record_success(
            test_db, SPEC, result(), "hash-a"
        )
        assert inserted is True
        assert test_db.query(RateSnapshot).count() == 1
        assert snapshot.payload_hash == "hash-a"

    def test_unchanged_hash_bumps_last_checked_only(self, test_db):
        first, _ = repo.record_success(test_db, SPEC, result(), "hash-a")
        original_fetched = first.fetched_at
        original_checked = first.last_checked_at

        second, inserted = repo.record_success(
            test_db, SPEC, result(), "hash-a"
        )

        assert inserted is False
        assert test_db.query(RateSnapshot).count() == 1
        assert second.id == first.id
        assert second.fetched_at == original_fetched
        assert second.last_checked_at >= original_checked

    def test_changed_hash_inserts_a_second_row(self, test_db):
        repo.record_success(test_db, SPEC, result("3450.50"), "hash-a")
        _, inserted = repo.record_success(
            test_db, SPEC, result("3460.00"), "hash-b"
        )

        assert inserted is True
        assert test_db.query(RateSnapshot).count() == 2

    def test_history_is_retained_not_overwritten(self, test_db):
        """The whole point of the rebuild: upstream overwrote one row
        per (bank, date), so nothing below daily resolution survived."""
        for index, rate in enumerate(["3450.00", "3455.00", "3460.00"]):
            repo.record_success(test_db, SPEC, result(rate), f"hash-{index}")

        rows = test_db.query(RateSnapshot).all()
        assert len(rows) == 3
        assert {r.quotes[0]["rate"] for r in rows} == {
            "3450.00",
            "3455.00",
            "3460.00",
        }


class TestStoredRepresentation:
    def test_rates_are_stored_as_strings(self, test_db):
        snapshot, _ = repo.record_success(
            test_db, SPEC, result("3450.50"), "hash-a"
        )
        quote = snapshot.quotes[0]
        assert isinstance(quote["rate"], str)
        assert quote["rate"] == "3450.50"
        assert isinstance(quote["unit_basis"], str)

    def test_trailing_zeros_survive_storage(self, test_db):
        snapshot, _ = repo.record_success(
            test_db, SPEC, result("3450.50"), "hash-a"
        )
        assert snapshot.quotes[1]["rate"] == "3480.00"

    def test_quotes_are_sorted_deterministically(self, test_db):
        snapshot, _ = repo.record_success(test_db, SPEC, result(), "hash-a")
        keys = [
            (q["currency"], q["channel"], q["side"]) for q in snapshot.quotes
        ]
        assert keys == sorted(keys)


class TestFailureIsolation:
    def test_failure_does_not_touch_the_last_snapshot(self, test_db):
        repo.record_success(test_db, SPEC, result("3450.50"), "hash-a")
        repo.record_failure(test_db, SPEC, "connection reset")

        snapshot = repo.latest_snapshot(test_db, SPEC.id)
        assert snapshot is not None
        assert snapshot.quotes[0]["rate"] == "3450.50"
        assert test_db.query(RateSnapshot).count() == 1

    def test_failures_accumulate(self, test_db):
        for _ in range(3):
            repo.record_failure(test_db, SPEC, "boom")
        state = repo.source_states(test_db)[SPEC.id]
        assert state.consecutive_failures == 3
        assert state.last_error == "boom"

    def test_success_resets_the_failure_streak(self, test_db):
        repo.record_failure(test_db, SPEC, "boom")
        repo.record_failure(test_db, SPEC, "boom")
        repo.record_success(test_db, SPEC, result(), "hash-a")

        state = repo.source_states(test_db)[SPEC.id]
        assert state.consecutive_failures == 0
        assert state.last_error is None

    def test_one_source_failing_leaves_others_alone(self, test_db):
        other = BY_ID["golomtbank"]
        repo.record_success(test_db, SPEC, result(), "hash-a")
        repo.record_success(
            test_db,
            other,
            CrawlResult(
                source_id=other.id,
                quotes=[Quote("USD", "cash", "buy", Decimal("3400"))],
                payload=b"{}",
            ),
            "hash-b",
        )
        repo.record_failure(test_db, SPEC, "down")

        latest = repo.latest_snapshots(test_db)
        assert set(latest) == {SPEC.id, other.id}
        assert latest[other.id].quotes[0]["rate"] == "3400"


class TestSourceSync:
    def test_sync_is_idempotent(self, test_db):
        repo.sync_sources(test_db)
        repo.sync_sources(test_db)
        from app.models.snapshot import Source

        assert test_db.query(Source).count() == len(BY_ID)
