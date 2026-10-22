"""Per-caller request budget for the public providers this API queries on a user's behalf."""

from __future__ import annotations

import time
from collections import OrderedDict

# Each process keeps its own counters. Several instances multiply the budget, so this bounds
# what one instance forwards to a provider; it is not a cluster-wide quota.
WINDOW_SECONDS = 60.0
# Enough callers for a classroom demonstration; the oldest window is dropped beyond it.
MAX_TRACKED_CALLERS = 1024


class FixedWindowLimiter:
    """Allows `limit` requests per caller within a window, counting from its first request."""

    def __init__(self, limit: int, window_seconds: float = WINDOW_SECONDS, max_callers: int = MAX_TRACKED_CALLERS) -> None:
        self._limit = max(0, limit)
        self._window = window_seconds
        self._max_callers = max(1, max_callers)
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    @property
    def limit(self) -> int:
        return self._limit

    def allow(self, caller: str, now: float | None = None) -> bool:
        """Records one request and reports whether it fits in the caller's current window."""
        if self._limit <= 0:
            return True
        moment = time.monotonic() if now is None else now
        started, used = self._windows.get(caller, (moment, 0))
        if moment - started >= self._window:
            started, used = moment, 0
        allowed = used < self._limit
        self._windows[caller] = (started, used + 1 if allowed else used)
        self._windows.move_to_end(caller)
        self._forget_expired(moment)
        return allowed

    def _forget_expired(self, moment: float) -> None:
        for caller, (started, _) in list(self._windows.items()):
            if moment - started >= self._window:
                del self._windows[caller]
        while len(self._windows) > self._max_callers:
            self._windows.popitem(last=False)
