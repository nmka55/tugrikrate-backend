"""Cadence and the ok/stale/failing classification."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import config
from app.models.snapshot import RateSnapshot, SourceState
from app.services.freshness import (
    STATUS_FAILING,
    STATUS_OK,
    STATUS_STALE,
    compute_status,
    interval_minutes,
    is_active_hours,
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


class TestActiveHours:
    @pytest.mark.parametrize("hour", [8, 12, 19])
    def test_banking_hours_are_active(self, hour):
        assert is_active_hours(at_local(hour)) is True

    @pytest.mark.parametrize("hour", [0, 7, 20, 23])
    def test_outside_banking_hours_is_not(self, hour):
        assert is_active_hours(at_local(hour)) is False

    def test_boundaries_follow_ulaanbaatar_not_utc(self):
        # 08:00 ULT is 00:00 UTC; classifying by UTC hour would call
        # this off-peak and crawl the whole trading morning hourly.
        assert is_active_hours(at_local(8)) is True
        assert at_local(8).astimezone(timezone.utc).hour == 0


class TestIntervals:
    def test_fast_source_active(self):
        assert interval_minutes(FAST, at_local(10)) == (
            config.CRAWL_ACTIVE_INTERVAL_MINUTES
        )

    def test_fast_source_offpeak(self):
        assert interval_minutes(FAST, at_local(2)) == (
            config.CRAWL_OFFPEAK_INTERVAL_MINUTES
        )

    def test_browser_source_runs_less_often(self):
        assert interval_minutes(SLOW, at_local(10)) == (
            config.CRAWL_ACTIVE_INTERVAL_MINUTES
            * config.CRAWL_PLAYWRIGHT_MULTIPLIER
        )
        assert interval_minutes(SLOW, at_local(10)) > interval_minutes(
            FAST, at_local(10)
        )


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

    def test_browser_source_gets_a_longer_allowance(self):
        """The same gap means different things for the two cadences.

        A fast source allows 30m x STALE_AFTER_INTERVALS (90 min) before
        it is stale; a browser source allows that times the Playwright
        multiplier. A 120-minute gap sits between the two.
        """
        now = at_local(10)
        fast_allowance = interval_minutes(FAST, now) * (
            config.STALE_AFTER_INTERVALS
        )
        slow_allowance = interval_minutes(SLOW, now) * (
            config.STALE_AFTER_INTERVALS
        )
        assert fast_allowance < 120 < slow_allowance

        checked = now - timedelta(minutes=120)
        assert compute_status(SLOW, snapshot(checked), None, now) == STATUS_OK
        assert (
            compute_status(FAST, snapshot(checked), None, now) == STATUS_STALE
        )


class TestDailyInternationalSource:
    """Frankfurter publishes once a day, so banking hours mean nothing
    to it and a weekend-old date is not an outage."""

    DAILY = BY_ID["frankfurter"]

    def test_interval_ignores_banking_hours(self):
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
