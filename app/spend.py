"""Per-account spending budgets for the providers this API calls on a user's behalf.

With SHARED_BUDGET_ENABLED=enabled, PostgreSQL is the source of truth: pf_spend counts each account's
window atomically, so every instance sees the same budget, plus an optional global cap (the assistant's
daily limit). The call carries the caller's JWT, so the database charges that account and nobody else.

When the database cannot answer (network, timeout, schema not migrated yet, unexpected reply), the
request falls back to this process's in-memory counters, which is how the budget worked before. This is
a deliberate choice, not failing closed: the providers are free public services whose own limits
remain, the in-memory window still bounds each instance, and a database outage would otherwise switch
off the assistant, UniProt search and prediction for everyone. Each fallback is logged without data.
An invalid token is never a fallback: it stays a 401.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException

from .context import ServiceContext
from .projects import data_request
from .rate_limit import FixedWindowLimiter
from .spend_config import (
    DAY_SECONDS, GLOBAL_FALLBACK_SUBJECT, GLOBAL_SPEND_CEILING, SPEND_CEILINGS, SPEND_REMAINING_RPC, SPEND_RPC,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Budget:
    """One scope's limits. A limit of 0 disables that part; both 0 means nothing is counted."""

    scope: str
    limit: int
    window_seconds: int
    global_limit: int = 0
    global_window_seconds: int = DAY_SECONDS

    @property
    def caller_limit(self) -> int:
        """The per-account limit PostgreSQL applies: the setting, clamped to the scope's ceiling."""
        return min(self.limit, SPEND_CEILINGS[self.scope]) if self.limit > 0 else 0

    @property
    def counted(self) -> bool:
        return self.limit > 0 or self.global_limit > 0


@dataclass(frozen=True)
class Spend:
    allowed: bool
    # What the account may still spend in its current window; None when there is no per-account limit.
    remaining: int | None


def _uses_database(context: ServiceContext) -> bool:
    return context.settings.shared_budget_enabled and context.settings.identity_enabled


def _fall_back(scope: str, reason: str) -> None:
    logger.warning("Shared budget unavailable for %s, using this process's counters: %s", scope, reason)


async def spend(context: ServiceContext, token: str, caller: str, budget: Budget, limiter: FixedWindowLimiter, global_limiter: FixedWindowLimiter | None = None) -> Spend:
    """Charges one request to the caller, or refuses it; call only once the request is valid and not cached."""
    if not budget.counted:
        return Spend(True, None)
    if _uses_database(context):
        try:
            answer = await data_request(context, token, "POST", SPEND_RPC, area="budget", json={
                "p_scope": budget.scope, "p_limit": budget.caller_limit, "p_window_seconds": budget.window_seconds,
                "p_global_limit": min(budget.global_limit, GLOBAL_SPEND_CEILING), "p_global_window_seconds": budget.global_window_seconds,
            })
            allowed, remaining = answer["allowed"], answer["remaining"]
            if not isinstance(allowed, bool) or not isinstance(remaining, int) or isinstance(remaining, bool):
                raise TypeError("Unexpected budget answer")
            return Spend(allowed, max(0, remaining) if budget.limit > 0 else None)
        except HTTPException as error:
            if error.status_code == 401:
                raise
            _fall_back(budget.scope, str(error.detail))
        except (KeyError, TypeError) as error:
            _fall_back(budget.scope, type(error).__name__)
    return _spend_in_memory(caller, limiter, global_limiter)


def _spend_in_memory(caller: str, limiter: FixedWindowLimiter, global_limiter: FixedWindowLimiter | None) -> Spend:
    # No await below: within one process these steps cannot interleave with another request.
    if limiter.remaining(caller) == 0:
        return Spend(False, 0)
    if global_limiter is not None and not global_limiter.allow(GLOBAL_FALLBACK_SUBJECT):
        return Spend(False, limiter.remaining(caller))
    limiter.allow(caller)
    return Spend(True, limiter.remaining(caller))


async def remaining(context: ServiceContext, token: str, caller: str, budget: Budget, limiter: FixedWindowLimiter) -> int | None:
    """What the caller may still spend now, without charging anything; None without a per-account limit."""
    if budget.limit <= 0:
        return None
    if _uses_database(context):
        try:
            answer = await data_request(context, token, "POST", SPEND_REMAINING_RPC, area="budget", json={
                "p_scope": budget.scope, "p_limit": budget.caller_limit, "p_window_seconds": budget.window_seconds,
            })
            if not isinstance(answer, int) or isinstance(answer, bool):
                raise TypeError("Unexpected budget answer")
            return max(0, answer)
        except HTTPException as error:
            if error.status_code == 401:
                raise
            _fall_back(budget.scope, str(error.detail))
        except TypeError as error:
            _fall_back(budget.scope, type(error).__name__)
    return limiter.remaining(caller)
