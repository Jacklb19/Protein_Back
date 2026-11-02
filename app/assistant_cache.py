"""Short-lived, process-local cache of verified readings, so an identical request does not reach the provider again.

Keys are opaque hashes built by the assistant: the facts, sequence and caller behind them are never kept.
Only readings that passed the verifier are stored; refusals and provider errors never are.
"""

from __future__ import annotations

from collections import OrderedDict
from time import monotonic
from typing import TYPE_CHECKING, Callable

from .config import DEFAULT_ASSISTANT_CACHE_MAX_ENTRIES, DEFAULT_ASSISTANT_CACHE_TTL_SECONDS

if TYPE_CHECKING:
    from .assistant import Interpretation


class AssistantCache:
    """Least recently used entries, each expiring a fixed time after it was stored; a hit does not extend it."""

    def __init__(self, ttl_seconds: float = DEFAULT_ASSISTANT_CACHE_TTL_SECONDS, max_entries: int = DEFAULT_ASSISTANT_CACHE_MAX_ENTRIES, clock: Callable[[], float] = monotonic) -> None:
        self._ttl = max(0.0, ttl_seconds)
        self._max_entries = max(0, max_entries)
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, Interpretation]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0 and self._max_entries > 0

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str) -> Interpretation | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, interpretation = item
        if expires <= self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return interpretation

    def put(self, key: str, interpretation: Interpretation) -> None:
        if not self.enabled:
            return
        now = self._clock()
        for stored, (expires, _) in list(self._items.items()):
            if expires <= now:
                del self._items[stored]
        self._items.pop(key, None)
        while len(self._items) >= self._max_entries:
            self._items.popitem(last=False)
        self._items[key] = (now + self._ttl, interpretation)
