"""The daily outbound-call ceiling."""

from datetime import date

import pytest

from app.utils.call_budget import (
    CallBudgetExceeded,
    DailyCallBudget,
    planned_daily_calls,
)


class TestDailyCallBudget:
    def test_spends_up_to_the_limit_then_refuses(self):
        budget = DailyCallBudget(3)
        budget.spend()
        budget.spend(2)
        assert budget.used == 3 and budget.remaining == 0
        with pytest.raises(CallBudgetExceeded):
            budget.spend()

    def test_a_refused_call_is_not_counted(self):
        budget = DailyCallBudget(2)
        budget.spend()
        with pytest.raises(CallBudgetExceeded):
            budget.spend(5)
        assert budget.used == 1

    def test_resets_on_a_new_utc_day(self, monkeypatch):
        budget = DailyCallBudget(1)
        days = iter([date(2026, 9, 29), date(2026, 9, 29), date(2026, 9, 30)])
        monkeypatch.setattr(
            DailyCallBudget, "_today", staticmethod(lambda: next(days))
        )
        budget.spend()
        with pytest.raises(CallBudgetExceeded):
            budget.spend()
        budget.spend()  # next day

    def test_rejects_a_nonsensical_limit(self):
        with pytest.raises(ValueError):
            DailyCallBudget(0)


class TestPlannedDailyCalls:
    def test_twelve_hourly_is_two_crawls_a_day(self):
        assert planned_daily_calls(12, 162) == 324

    def test_non_divisor_interval_rounds_up(self):
        # 24 // 7 == 3 would under-count; 4 crawls can actually fire.
        assert planned_daily_calls(7, 100) == 400
