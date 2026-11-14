"""Authenticated, consented access to the public ESM Atlas server. No private-sequence cache."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from .folding_config import (
    FOLDING_CONTENT_TYPES, FOLDING_PROCESS_REQUESTS_PER_MINUTE, FOLDING_REQUESTS_PER_MINUTE,
    FOLDING_TIMEOUT_SECONDS, FOLDING_URL, FOLDING_WINDOW_SECONDS, MAX_FOLDING_BYTES, MAX_FOLDING_CALLERS,
)
from .folding_models import FoldRequest, FoldResult, sequence_hash
from .context import context_from_state
from .identity import CurrentIdentity, bearer_token
from .rate_limit import FixedWindowLimiter
from .spend import Budget, spend
from .spend_config import SCOPE_FOLDING


class FoldingGate:
    """Atomic in-process admission, not a distributed quota or persistent queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._callers: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._process = (0.0, 0)

    def acquire(self, caller: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            if self._active:
                return False
            started, used = self._callers.get(caller, (moment, 0))
            process_started, process_used = self._process
            if moment - started >= FOLDING_WINDOW_SECONDS:
                started, used = moment, 0
            if moment - process_started >= FOLDING_WINDOW_SECONDS:
                process_started, process_used = moment, 0
            if used >= FOLDING_REQUESTS_PER_MINUTE or process_used >= FOLDING_PROCESS_REQUESTS_PER_MINUTE:
                return False
            self._callers[caller] = (started, used + 1)
            self._callers.move_to_end(caller)
            while len(self._callers) > MAX_FOLDING_CALLERS:
                self._callers.popitem(last=False)
            self._process = (process_started, process_used + 1)
            self._active = True
            return True

    def release(self) -> None:
        with self._lock:
            self._active = False


# Cluster-wide counterpart of the gate's per-caller and overall windows, counted in PostgreSQL when enabled.
FOLDING_BUDGET = Budget(SCOPE_FOLDING, FOLDING_REQUESTS_PER_MINUTE, int(FOLDING_WINDOW_SECONDS), FOLDING_PROCESS_REQUESTS_PER_MINUTE, int(FOLDING_WINDOW_SECONDS))


async def predict(state, body: FoldRequest, caller: str, token: str) -> FoldResult:
    if not body.consent_to_send_sequence:
        raise HTTPException(403, "folding-consent-required")
    if not state.settings.folding_enabled:
        raise HTTPException(503, "folding-not-configured")
    if not state.folding_gate.acquire(caller):
        raise HTTPException(429, "folding-rate-limit")
    try:
        # The gate already counted this request in memory, so it is the fallback: an unreachable database
        # leaves the in-process windows in charge (see app/spend.py).
        if state.settings.shared_budget_enabled:
            spent = await spend(context_from_state(state), token, caller, FOLDING_BUDGET, FixedWindowLimiter(0))
            if not spent.allowed:
                raise HTTPException(429, "folding-rate-limit")
        # HTTPX phase timeouts alone allow a provider to keep the request alive by
        # sending tiny chunks. Bound the complete exchange as well.
        async with asyncio.timeout(FOLDING_TIMEOUT_SECONDS):
            return await _exchange(state.http_client, body)
    except (TimeoutError, httpx.TimeoutException) as error:
        raise HTTPException(504, "folding-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "folding-provider-error") from error
    finally:
        state.folding_gate.release()


async def _exchange(client: httpx.AsyncClient, body: FoldRequest) -> FoldResult:
    async with client.stream(
        "POST", FOLDING_URL, content=body.sequence.encode("ascii"),
        headers={"Content-Type": "text/plain", "Accept": "chemical/x-pdb", "Accept-Encoding": "identity"},
        timeout=FOLDING_TIMEOUT_SECONDS, follow_redirects=False,
    ) as upstream:
        if upstream.status_code == 429:
            raise HTTPException(429, "folding-rate-limit")
        if upstream.status_code != 200:
            raise HTTPException(502, "folding-provider-error")
        if upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in FOLDING_CONTENT_TYPES:
            raise HTTPException(502, "folding-invalid-response")
        if upstream.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise HTTPException(502, "folding-invalid-response")
        chunks = bytearray()
        async for chunk in upstream.aiter_bytes():
            if len(chunks) + len(chunk) > MAX_FOLDING_BYTES:
                raise HTTPException(502, "folding-invalid-response")
            chunks.extend(chunk)
    try:
        pdb = chunks.decode("utf-8", errors="strict")
        return FoldResult(
            sequence=body.sequence, sequence_sha256=sequence_hash(body.sequence), pdb=pdb,
            pdb_sha256=hashlib.sha256(chunks).hexdigest(), retrieved_at=datetime.now(timezone.utc).isoformat(),
        )
    except (UnicodeError, ValueError, ValidationError) as error:
        raise HTTPException(502, "folding-invalid-response") from error


router = APIRouter(tags=["predictions"])


@router.post("/predictions/fold", response_model=FoldResult)
async def fold(body: FoldRequest, identity: CurrentIdentity, request: Request, response: Response) -> FoldResult:
    result = await predict(request.app.state, body, identity.id, bearer_token(request))
    response.headers["Cache-Control"] = "no-store"
    return result
