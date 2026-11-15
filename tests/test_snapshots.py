"""Snapshot persistence: insert on change, bump on no-change, and keep
a failing source's last good data intact."""

from datetime import datetime, timedelta, timezone
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

    def test_unchanged_quotes_bump_last_checked_only(self, test_db):
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

    def test_changed_quotes_insert_a_second_row(self, test_db):
        repo.record_success(test_db, SPEC, result("3450.50"), "hash-a")
        _, inserted = repo.record_success(
            test_db, SPEC, result("3460.00"), "hash-b"
        )

        assert inserted is True
        assert test_db.query(RateSnapshot).count() == 2

    def test_same_quotes_under_a_new_payload_hash_do_not_insert(self, test_db):
        """Golomt's payload carries tvalue/diff/status fields the feed
        never publishes; they moved the hash on almost every crawl and
        431 of its 499 newest snapshots repeated the one before."""
        first, _ = repo.record_success(test_db, SPEC, result(), "hash-a")

        second, inserted = repo.record_success(
            test_db, SPEC, result(), "hash-b"
        )

        assert inserted is False
        assert test_db.query(RateSnapshot).count() == 1
        assert second.id == first.id
        # The hash stays the one the quotes were first seen under.
        assert second.payload_hash == "hash-a"

    def test_new_quotes_under_the_same_payload_hash_insert(self, test_db):
        """A corrected mapping changes the quotes a payload yields; it
        must reach the feed on the next crawl, not wait for the bank."""
        repo.record_success(test_db, SPEC, result("3450.50"), "hash-a")
        snapshot, inserted = repo.record_success(
            test_db, SPEC, result("3460.00"), "hash-a"
        )

        assert inserted is True
        assert test_db.query(RateSnapshot).count() == 2
        assert snapshot.quotes[0]["rate"] == "3460.00"

    def test_a_restated_date_is_recorded_without_a_new_row(self, test_db):
        older = datetime(2026, 11, 13, 16, tzinfo=timezone.utc)
        newer = datetime(2026, 11, 14, 16, tzinfo=timezone.utc)
        repo.record_success(
            test_db, SPEC, result(published_at=older), "hash-a"
        )
        snapshot, inserted = repo.record_success(
            test_db, SPEC, result(published_at=newer), "hash-b"
        )

        assert inserted is False
        assert snapshot.published_at == newer

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


def backdate(db, snapshot, days):
    snapshot.fetched_at = snapshot.fetched_at - timedelta(days=days)
    db.commit()


class TestLatestPerSource:
    def test_returns_only_the_newest_row_of_each_source(self, test_db):
        other = BY_ID["golomtbank"]
        for rate in ("3450.00", "3455.00", "3460.00"):
            repo.record_success(test_db, SPEC, result(rate), "h")
        repo.record_success(
            test_db,
            other,
            CrawlResult(
                source_id=other.id,
                quotes=[Quote("USD", "cash", "buy", Decimal("3400"))],
            ),
            "h",
        )

        latest = repo.latest_snapshots(test_db)

        assert set(latest) == {SPEC.id, other.id}
        assert latest[SPEC.id].quotes[0]["rate"] == "3460.00"
        assert latest[other.id].quotes[0]["rate"] == "3400"

    def test_agrees_with_the_single_source_lookup(self, test_db):
        """Same order as latest_snapshot: fetched_at, then id."""
        first, _ = repo.record_success(test_db, SPEC, result("1.00"), "h")
        second, _ = repo.record_success(test_db, SPEC, result("2.00"), "h")
        # An equal timestamp falls back to the higher id...
        second.fetched_at = first.fetched_at
        test_db.commit()
        assert repo.latest_snapshots(test_db)[SPEC.id].id == second.id
        # ...and a later fetch wins whatever its id.
        backdate(test_db, second, days=1)
        assert repo.latest_snapshots(test_db)[SPEC.id].id == first.id
        assert repo.latest_snapshot(test_db, SPEC.id).id == first.id

    def test_no_snapshots_is_an_empty_mapping(self, test_db):
        assert repo.latest_snapshots(test_db) == {}


class TestPrune:
    def test_zero_keeps_everything(self, test_db):
        snapshot, _ = repo.record_success(test_db, SPEC, result("1.00"), "h")
        backdate(test_db, snapshot, days=400)
        repo.record_success(test_db, SPEC, result("2.00"), "h")

        assert repo.prune_snapshots(test_db, 0) == 0
        assert test_db.query(RateSnapshot).count() == 2

    def test_drops_only_rows_past_the_cutoff(self, test_db):
        old, _ = repo.record_success(test_db, SPEC, result("1.00"), "h")
        backdate(test_db, old, days=40)
        recent, _ = repo.record_success(test_db, SPEC, result("2.00"), "h")
        backdate(test_db, recent, days=5)
        repo.record_success(test_db, SPEC, result("3.00"), "h")

        assert repo.prune_snapshots(test_db, 30) == 1
        rates = {
            row.quotes[0]["rate"] for row in test_db.query(RateSnapshot).all()
        }
        assert rates == {"2.00", "3.00"}

    def test_a_quiet_source_keeps_its_last_snapshot(self, test_db):
        """A source that last changed long ago must not vanish from the
        feed because its only snapshot aged out."""
        only, _ = repo.record_success(test_db, SPEC, result(), "h")
        backdate(test_db, only, days=400)

        assert repo.prune_snapshots(test_db, 30) == 0
        assert repo.latest_snapshot(test_db, SPEC.id).id == only.id


class TestSourceSync:
    def test_sync_is_idempotent(self, test_db):
        repo.sync_sources(test_db)
        repo.sync_sources(test_db)
        from app.models.snapshot import Source

        assert test_db.query(Source).count() == len(BY_ID)
