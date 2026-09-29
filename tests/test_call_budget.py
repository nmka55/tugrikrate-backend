"""The daily outbound-call ceiling."""

from datetime import date

import pytest

from app.utils.call_budget import CallBudgetExceeded, DailyCallBudget


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
