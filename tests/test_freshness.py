"""Cadence and the ok/stale/failing classification."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import config
from app.models.snapshot import RateSnapshot, SourceState
from app.services.freshness import (
    STATUS_FAILING,
    STATUS_OK,
    STATUS_STALE,
    compute_status,
    interval_minutes,
)
from app.sources.registry import BY_ID

ULT = ZoneInfo("Asia/Ulaanbaatar")
FAST = BY_ID["khanbank"]
SLOW = BY_ID["tdbm"]


def at_local(hour, minute=0):
    return datetime(2026, 9, 29, hour, minute, tzinfo=ULT).astimezone(
        timezone.utc
    )


def snapshot(checked_at, published_at=None):
    return RateSnapshot(
        source_id=FAST.id,
        fetched_at=checked_at,
        last_checked_at=checked_at,
        published_at=published_at,
        payload_hash="h",
        quotes=[],
    )


class TestIntervals:
    """A bank's interval is the gap between the slots surrounding now."""

    def test_json_bank_is_45_min_in_working_hours(self):
        assert interval_minutes(FAST, at_local(10)) == 45

    def test_json_bank_overnight_gaps(self):
        assert interval_minutes(FAST, at_local(20)) == 360  # 18:00-00:00
        assert interval_minutes(FAST, at_local(7)) == 180  # 06:00-09:00

    def test_browser_bank_is_3h_by_day_and_5h_overnight(self):
        assert interval_minutes(SLOW, at_local(10)) == 180
        assert interval_minutes(SLOW, at_local(20)) == 300  # 18:00-23:00
        assert interval_minutes(SLOW, at_local(23, 30)) == 300  # to 04:00

    def test_a_slot_belongs_to_the_gap_it_starts(self):
        assert interval_minutes(FAST, at_local(9, 45)) == 45

    def test_follows_ulaanbaatar_not_utc(self):
        # 10:00 ULT is 02:00 UTC; the UTC clock would read overnight.
        assert interval_minutes(FAST, at_local(10)) == 45


class TestStatus:
    def test_recent_check_is_ok(self):
        now = at_local(10)
        assert (
            compute_status(
                FAST, snapshot(now - timedelta(minutes=5)), None, now
            )
            == STATUS_OK
        )

    def test_old_check_is_stale(self):
        now = at_local(10)
        old = now - timedelta(minutes=500)
        assert compute_status(FAST, snapshot(old), None, now) == STATUS_STALE

    def test_no_snapshot_is_failing(self):
        assert compute_status(FAST, None, None, at_local(10)) == (
            STATUS_FAILING
        )

    def test_repeated_failures_mark_failing(self):
        now = at_local(10)
        state = SourceState(
            source_id=FAST.id,
            consecutive_failures=config.FAILING_AFTER_ATTEMPTS,
        )
        assert (
            compute_status(
                FAST, snapshot(now - timedelta(minutes=1)), state, now
            )
            == STATUS_FAILING
        )

    def test_a_single_failure_does_not_mark_failing(self):
        now = at_local(10)
        state = SourceState(source_id=FAST.id, consecutive_failures=1)
        assert (
            compute_status(
                FAST, snapshot(now - timedelta(minutes=1)), state, now
            )
            == STATUS_OK
        )

    def test_reachable_source_publishing_old_rates_is_stale(self):
        """Naiman Sharga's case: the crawl succeeds every 15 minutes
        but the document it serves was published days ago."""
        now = at_local(10)
        stale_publication = now - timedelta(
            hours=config.PUBLISHED_STALE_HOURS + 1
        )
        assert (
            compute_status(
                FAST,
                snapshot(now - timedelta(minutes=1), stale_publication),
                None,
                now,
            )
            == STATUS_STALE
        )

    def test_recent_publication_stays_ok(self):
        now = at_local(10)
        assert (
            compute_status(
                FAST,
                snapshot(now - timedelta(minutes=1), now - timedelta(hours=2)),
                None,
                now,
            )
            == STATUS_OK
        )

    def test_allowance_follows_the_gap(self):
        """A bank is held to 3 x the gap it is currently in: 135 min by
        day, 18 h in the 6 h evening gap."""
        now = at_local(10)
        assert (
            compute_status(
                FAST, snapshot(now - timedelta(minutes=120)), None, now
            )
            == STATUS_OK
        )
        assert (
            compute_status(
                FAST, snapshot(now - timedelta(minutes=150)), None, now
            )
            == STATUS_STALE
        )
        night = at_local(20)
        assert (
            compute_status(
                FAST, snapshot(night - timedelta(hours=10)), None, night
            )
            == STATUS_OK
        )


class TestDailyInternationalSource:
    """Frankfurter publishes once a day, so banking hours mean nothing
    to it and a weekend-old date is not an outage."""

    DAILY = BY_ID["frankfurter"]

    def test_interval_ignores_bank_slots(self):
        # 4 fetches a day => one every 6 hours, day and night.
        assert interval_minutes(self.DAILY, at_local(10)) == 360
        assert interval_minutes(self.DAILY, at_local(2)) == 360

    def test_a_three_day_old_date_is_still_ok_over_a_weekend(self):
        now = at_local(10)
        published = now - timedelta(hours=72)
        assert (
            compute_status(
                self.DAILY,
                snapshot(now - timedelta(minutes=30), published),
                None,
                now,
            )
            == STATUS_OK
        )

    def test_but_the_same_age_would_stale_a_bank(self):
        now = at_local(10)
        published = now - timedelta(hours=72)
        assert (
            compute_status(
                FAST,
                snapshot(now - timedelta(minutes=1), published),
                None,
                now,
            )
            == STATUS_STALE
        )

    def test_a_date_older_than_its_own_limit_is_stale(self):
        now = at_local(10)
        published = now - timedelta(hours=self.DAILY.published_stale_hours + 1)
        assert (
            compute_status(
                self.DAILY,
                snapshot(now - timedelta(minutes=30), published),
                None,
                now,
            )
            == STATUS_STALE
        )

    def test_missing_two_of_its_crawls_makes_it_stale(self):
        now = at_local(10)
        checked = now - timedelta(
            minutes=interval_minutes(self.DAILY, now)
            * config.STALE_AFTER_INTERVALS
            + 5
        )
        assert (
            compute_status(self.DAILY, snapshot(checked), None, now)
            == STATUS_STALE
        )
