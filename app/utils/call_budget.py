"""A hard ceiling on outbound requests per day.

Cron cadence alone is not a limit: a bug, a restart loop or a manual
admin crawl can all issue calls the schedule never planned. This is the
backstop - every outbound request to a metered source is `spend()`-ed
first, and the call that would cross the ceiling raises instead of
being sent.

State is in-process and resets at 00:00 UTC, matching the single-process
assumption documented in CLAUDE.md. It is a guard against runaway
behaviour inside one process, not a distributed quota.
"""

import threading
from datetime import date, datetime, timezone


class CallBudgetExceeded(RuntimeError):
    """Raised instead of sending a request that would pass the limit."""


class DailyCallBudget:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self._day: date | None = None
        self._used = 0
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _roll(self) -> None:
        today = self._today()
        if self._day != today:
            self._day, self._used = today, 0

    @property
    def used(self) -> int:
        with self._lock:
            self._roll()
            return self._used

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def spend(self, calls: int = 1) -> None:
        with self._lock:
            self._roll()
            if self._used + calls > self.limit:
                raise CallBudgetExceeded(
                    f"daily call budget of {self.limit} would be exceeded "
                    f"({self._used} used, {calls} requested)"
                )
            self._used += calls
