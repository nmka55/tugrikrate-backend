"""Scheduler wiring: cadence windows, jitter, and overlap protection."""

from collections import Counter

from app.config import config
from app.services.scheduler import (
    _active_hours,
    _cron_fields,
    _offpeak_hours,
    build_scheduler,
)
from app.sources.registry import FAST_SPECS, SLOW_SPECS, specs_for_group


class TestHourWindows:
    def test_active_and_offpeak_partition_the_day(self):
        assert sorted(_active_hours() + _offpeak_hours()) == list(range(24))

    def test_active_window_matches_config(self):
        assert _active_hours() == list(
            range(config.CRAWL_ACTIVE_START_HOUR, config.CRAWL_ACTIVE_END_HOUR)
        )


class TestCronFields:
    def test_sub_hourly_interval_uses_a_minute_step(self):
        fields = _cron_fields(15, [8, 9, 10])
        assert fields["minute"] == "*/15"
        assert fields["hour"] == "8,9,10"

    def test_hourly_interval_fires_on_the_hour(self):
        assert _cron_fields(60, [1, 2])["minute"] == "0"

    def test_multi_hour_interval_thins_the_hour_list(self):
        fields = _cron_fields(240, list(range(24)))
        assert fields["minute"] == "0"
        assert fields["hour"] == "0,4,8,12,16,20"

    def test_empty_window_yields_no_job(self):
        assert _cron_fields(15, []) is None

    def test_multi_hour_interval_always_keeps_one_slot(self):
        # A narrow window whose hours never hit the stride must still
        # fire, rather than silently never running.
        fields = _cron_fields(240, [9, 10])
        assert fields["hour"]


class TestSchedulerJobs:
    def test_registers_all_four_cadence_jobs(self):
        scheduler = build_scheduler()
        ids = {job.id for job in scheduler.get_jobs()}
        assert ids == {
            "http-active",
            "http-offpeak",
            "browser-active",
            "browser-offpeak",
        }

    def test_browser_and_http_sources_are_split(self):
        scheduler = build_scheduler()
        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert jobs["http-active"].args[0] == FAST_SPECS
        assert jobs["browser-active"].args[0] == SLOW_SPECS
        assert len(SLOW_SPECS) == 5
        assert len(FAST_SPECS) == 10

    def test_jobs_do_not_stack_on_themselves(self):
        for job in build_scheduler().get_jobs():
            assert job.max_instances == 1
            assert job.coalesce is True

    def test_triggers_carry_jitter(self):
        """Without jitter every source would hit each bank on the exact
        same wall-clock boundary, every quarter hour, forever."""
        for job in build_scheduler().get_jobs():
            assert job.trigger.jitter == config.CRAWL_JITTER_SECONDS

    def test_triggers_use_mongolian_local_time(self):
        for job in build_scheduler().get_jobs():
            assert str(job.trigger.timezone) == config.CRAWL_TIMEZONE


class TestSourceGroups:
    """Splitting the Playwright sources into their own process."""

    def test_group_all_owns_every_source(self):
        assert len(specs_for_group("all")) == 15

    def test_fast_and_slow_partition_all_sources(self):
        fast = set(specs_for_group("fast"))
        slow = set(specs_for_group("slow"))
        assert fast | slow == set(specs_for_group("all"))
        assert fast & slow == set()

    def test_slow_group_is_exactly_the_playwright_sources(self):
        assert {s.id for s in specs_for_group("slow")} == {
            "tdbm",
            "bogdbank",
            "ckbank",
            "nibank",
            "transbank",
        }

    def test_fast_process_schedules_no_browser_jobs(self):
        ids = {job.id for job in build_scheduler("fast").get_jobs()}
        assert ids == {"http-active", "http-offpeak"}

    def test_slow_process_schedules_only_browser_jobs(self):
        ids = {job.id for job in build_scheduler("slow").get_jobs()}
        assert ids == {"browser-active", "browser-offpeak"}

    def test_split_processes_cover_every_source_exactly_once(self):
        """The safety property that lets the two run uncoordinated:
        no source is scheduled by both, and none is dropped."""
        collected = []
        for group in ("fast", "slow"):
            for job in build_scheduler(group).get_jobs():
                collected.extend(spec.id for spec in job.args[0])

        # Each source appears once per time-window job (active +
        # offpeak), so exactly twice overall - never in both groups.
        counts = Counter(collected)
        assert len(counts) == 15
        assert set(counts.values()) == {2}


class TestInvalidGroup:
    def test_unknown_group_falls_back_to_all(self):
        # config validates the env var; specs_for_group itself is
        # permissive so a stray value can never silently collect
        # nothing.
        assert len(specs_for_group("nonsense")) == 15
