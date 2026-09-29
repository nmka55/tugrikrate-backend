"""Scheduler wiring: cadence windows, jitter, and overlap protection."""

from collections import Counter

from app.config import config
from app.services.scheduler import (
    _active_hours,
    _cron_fields,
    _offpeak_hours,
    build_scheduler,
)
from app.sources.registry import (
    DAILY_SPECS,
    FAST_SPECS,
    SLOW_SPECS,
    specs_for_group,
)


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
    def test_registers_all_five_cadence_jobs(self):
        scheduler = build_scheduler()
        ids = {job.id for job in scheduler.get_jobs()}
        assert ids == {
            "http-active",
            "http-offpeak",
            "browser-active",
            "browser-offpeak",
            "intl-daily",
        }

    def test_browser_and_http_sources_are_split(self):
        scheduler = build_scheduler()
        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert jobs["http-active"].args[0] == FAST_SPECS
        assert jobs["browser-active"].args[0] == SLOW_SPECS
        assert len(SLOW_SPECS) == 5
        assert len(FAST_SPECS) == 10
        assert [s.id for s in DAILY_SPECS] == ["frankfurter", "fxratesapi"]
        # fxratesapi has no key in tests, so only Frankfurter runs.
        assert [s.id for s in jobs["intl-daily"].args[0]] == ["frankfurter"]

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
        assert len(specs_for_group("all")) == 17

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
        assert ids == {"http-active", "http-offpeak", "intl-daily"}

    def test_slow_process_schedules_only_browser_jobs(self):
        ids = {job.id for job in build_scheduler("slow").get_jobs()}
        assert ids == {"browser-active", "browser-offpeak"}

    def test_split_processes_cover_every_source_exactly_once(
        self, monkeypatch
    ):
        """The safety property that lets the two run uncoordinated:
        no source is scheduled by both, and none is dropped."""
        monkeypatch.setattr(config, "FXRATESAPI_KEY", "k")
        collected = []
        for group in ("fast", "slow"):
            for job in build_scheduler(group).get_jobs():
                collected.extend(spec.id for spec in job.args[0])

        # Each bank appears once per time-window job (active +
        # offpeak), so exactly twice overall - never in both groups.
        # The daily international sources share a single
        # around-the-clock job, so each appears exactly once.
        counts = Counter(collected)
        assert len(counts) == 17
        assert counts.pop("frankfurter") == 1
        assert counts.pop("fxratesapi") == 1
        assert set(counts.values()) == {2}

    def test_a_source_without_its_key_is_not_scheduled(self):
        collected = {
            spec.id
            for job in build_scheduler("all").get_jobs()
            for spec in job.args[0]
        }
        assert "fxratesapi" not in collected
        assert "frankfurter" in collected

    def test_a_source_with_its_key_is_scheduled(self, monkeypatch):
        monkeypatch.setattr(config, "FXRATESAPI_KEY", "k")
        jobs = {j.id: j for j in build_scheduler("all").get_jobs()}
        assert [s.id for s in jobs["intl-daily"].args[0]] == [
            "frankfurter",
            "fxratesapi",
        ]


class TestInvalidGroup:
    def test_unknown_group_falls_back_to_all(self):
        # config validates the env var; specs_for_group itself is
        # permissive so a stray value can never silently collect
        # nothing.
        assert len(specs_for_group("nonsense")) == 17


class TestInternationalSchedule:
    """The owner's rule: the foreign-exchange table is fetched 4 times a
    day, never on the banks' 15-minute cadence."""

    def intl_job(self):
        return {j.id: j for j in build_scheduler().get_jobs()}["intl-daily"]

    def test_default_is_four_fetches_a_day(self):
        assert config.INTL_CRAWLS_PER_DAY == 4
        fields = {f.name: str(f) for f in self.intl_job().trigger.fields}
        assert fields["hour"] == "0,6,12,18"
        assert fields["minute"] == "0"

    def test_the_default_schedule_fits_the_call_ceiling(self):
        from app.crawlers import frankfurter, fxratesapi

        # One request per fetch, and each source has its own budget.
        assert frankfurter.BUDGET is not fxratesapi.BUDGET
        for budget in (frankfurter.BUDGET, fxratesapi.BUDGET):
            assert config.INTL_CRAWLS_PER_DAY <= budget.limit

    def test_each_sources_budget_is_checked_at_startup(self, monkeypatch):
        import pytest

        from app.crawlers import fxratesapi

        monkeypatch.setattr(config, "FXRATESAPI_KEY", "k")
        monkeypatch.setattr(
            fxratesapi, "BUDGET", fxratesapi.DailyCallBudget(2)
        )
        with pytest.raises(ValueError, match="fxratesapi"):
            build_scheduler("fast")

    def test_a_schedule_that_would_exceed_the_ceiling_refuses_to_start(
        self, monkeypatch
    ):
        import pytest

        monkeypatch.setattr(config, "INTL_CRAWLS_PER_DAY", 24)
        with pytest.raises(ValueError, match="INTL_DAILY_CALL_LIMIT"):
            build_scheduler("fast")

    def test_crawls_per_day_must_divide_the_day(self, monkeypatch):
        import importlib.util

        import pytest

        import app.config as cfg

        # Load a private copy of the module rather than reloading
        # app.config: a reload rebinds `app.config.config` to a new
        # object that every module imported earlier does not see, which
        # silently breaks config monkeypatching in later tests.
        monkeypatch.setenv("INTL_CRAWLS_PER_DAY", "5")
        probe = importlib.util.spec_from_file_location(
            "_config_probe", cfg.__file__
        )
        with pytest.raises(ValueError, match="divide 24"):
            probe.loader.exec_module(importlib.util.module_from_spec(probe))
