"""Live channel over WebSocket: tells a signed-in browser that its projects changed elsewhere.

The channel carries no project data, only identifiers and timestamps the caller may already read. Each
connection looks at the caller's own rows with the caller's JWT, so PostgreSQL RLS decides what it sees and
nothing depends on memory shared between server instances; a write handled by this process only makes the
next look happen at once.

Browsers cannot set headers on a WebSocket, so the token travels in the first message, never in the URL,
and the handshake is accepted only from the configured frontend origins (CORS does not cover WebSockets).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .config import Settings
from .identity import Identity, resolve_identity
from .live_config import (
    CLIENT_AUTH, CLIENT_PING, CLOSE_FORBIDDEN, CLOSE_MALFORMED, CLOSE_MESSAGE_TOO_BIG, CLOSE_POLICY_VIOLATION, CLOSE_ROTATE,
    CLOSE_TIMEOUT, CLOSE_TOO_MANY, CLOSE_TRY_AGAIN_LATER, CLOSE_UNAUTHORIZED, CLOSE_UNSUPPORTED_DATA, LIVE_PATH,
    LIVE_PROJECTS_PATH, LIVE_SUBPROTOCOL, SERVER_PONG, SERVER_PROJECTS_CHANGED, SERVER_READY, LiveLimits,
)
from .rate_limit import FixedWindowLimiter

logger = logging.getLogger(__name__)
router = APIRouter()


class Closing(Exception):
    """Ends the connection with a close code and a fixed reason; reasons never quote what the client sent."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class LiveHub:
    """The connections this process holds, by owner. Other instances keep their own."""

    def __init__(self, connections_per_caller: int = LiveLimits().connections_per_caller) -> None:
        self._limit = max(1, connections_per_caller)
        self._wakes: dict[str, set[asyncio.Event]] = {}

    def join(self, owner: str) -> asyncio.Event | None:
        """Registers a connection and returns its wake signal; None when the owner already holds the limit."""
        wakes = self._wakes.setdefault(owner, set())
        if len(wakes) >= self._limit:
            return None
        wake = asyncio.Event()
        wakes.add(wake)
        return wake

    def leave(self, owner: str, wake: asyncio.Event) -> None:
        wakes = self._wakes.get(owner)
        if wakes is None:
            return
        wakes.discard(wake)
        if not wakes:
            del self._wakes[owner]

    def notify(self, owner: str | None) -> None:
        """Called after a write: the owner's connections look again now instead of at their next interval."""
        for wake in self._wakes.get(owner or "", ()):
            wake.set()

    def connections(self, owner: str) -> int:
        return len(self._wakes.get(owner, ()))


@dataclass(frozen=True)
class Snapshot:
    """What one look saw: the most recent projects and how many the caller has in all."""

    stamps: dict[str, str]
    total: int
    complete: bool

    @property
    def revision(self) -> str:
        body = json.dumps([self.total, sorted(self.stamps.items())], separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _total(content_range: str, fallback: int) -> int:
    """The count PostgREST reports after the slash of `Content-Range: 0-9/42`."""
    _, _, total = content_range.partition("/")
    return int(total) if total.isdigit() else fallback


class LiveSession:
    def __init__(self, websocket: WebSocket, settings: Settings, client: httpx.AsyncClient, hub: LiveHub) -> None:
        self._socket = websocket
        self._settings = settings
        self._limits = settings.live
        self._client = client
        self._hub = hub
        self._token = ""
        self._identity: Identity | None = None
        self._sending = asyncio.Lock()
        self._budget = FixedWindowLimiter(self._limits.messages_per_minute, max_callers=1)

    async def run(self) -> None:
        wake: asyncio.Event | None = None
        code, reason = CLOSE_ROTATE, "rotate"
        try:
            self._identity = await self._identify(await self._receive(self._limits.auth_timeout_seconds))
            wake = self._hub.join(self._identity.id)
            if wake is None:
                raise Closing(CLOSE_TOO_MANY, "too-many-connections")
            tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._watch(wake))]
            try:
                # The server ends the connection itself, in time for the client to open the next one cleanly.
                done, _pending = await asyncio.wait(tasks, timeout=self._settings.live_max_connection_seconds, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in done:
                if (error := task.exception()) is not None:
                    raise error
        except Closing as closing:
            code, reason = closing.code, closing.reason
        except WebSocketDisconnect:
            return
        except Exception:
            # Never the message: it can quote a provider answer.
            logger.exception("Live connection failed")
            code, reason = CLOSE_TRY_AGAIN_LATER, "internal"
        finally:
            if wake is not None and self._identity is not None:
                self._hub.leave(self._identity.id, wake)
        try:
            await self._socket.close(code=code, reason=reason)
        except RuntimeError:
            pass  # The client left first.

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._sending:
            await self._socket.send_text(json.dumps(message, separators=(",", ":")))

    async def _receive(self, timeout: float) -> dict[str, Any]:
        try:
            frame = await asyncio.wait_for(self._socket.receive(), timeout)
        except TimeoutError as error:
            raise Closing(CLOSE_TIMEOUT, "timeout") from error
        if frame["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(frame.get("code", 1000))
        text = frame.get("text")
        if text is None:
            raise Closing(CLOSE_UNSUPPORTED_DATA, "text-only")
        if len(text.encode("utf-8")) > self._limits.max_message_bytes:
            raise Closing(CLOSE_MESSAGE_TOO_BIG, "message-too-big")
        if not self._budget.allow("connection"):
            raise Closing(CLOSE_TOO_MANY, "too-many-messages")
        try:
            message = json.loads(text)
        except ValueError as error:
            raise Closing(CLOSE_MALFORMED, "malformed") from error
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise Closing(CLOSE_MALFORMED, "malformed")
        return message

    async def _identify(self, message: dict[str, Any]) -> Identity:
        """Validates the token of an `auth` message with Supabase, exactly as the HTTP routes do."""
        token = message.get("token")
        if message["type"] != CLIENT_AUTH or not isinstance(token, str) or not token:
            raise Closing(CLOSE_UNAUTHORIZED, "missing-token")
        try:
            identity = await resolve_identity(self._settings, self._client, token)
        except HTTPException as error:
            if error.status_code == 401:
                raise Closing(CLOSE_UNAUTHORIZED, str(error.detail)) from error
            raise Closing(CLOSE_TRY_AGAIN_LATER, str(error.detail)) from error
        if self._identity is not None and identity.id != self._identity.id:
            # A connection belongs to one account for its whole life.
            raise Closing(CLOSE_FORBIDDEN, "identity-changed")
        self._token = token
        return identity

    async def _read(self) -> None:
        idle = self._limits.heartbeat_seconds * self._limits.idle_factor
        while True:
            message = await self._receive(idle)
            if message["type"] == CLIENT_PING:
                await self._send({"type": SERVER_PONG})
            elif message["type"] == CLIENT_AUTH:
                # A refreshed token replaces the one this connection reads with.
                await self._identify(message)
            else:
                raise Closing(CLOSE_MALFORMED, "unknown-type")

    async def _look(self) -> Snapshot | None:
        """Reads the caller's most recent projects; None when the data service did not answer this time."""
        limit = self._limits.watched_projects
        headers = {"apikey": self._settings.supabase_anon_key, "Authorization": f"Bearer {self._token}", "Accept": "application/json", "Prefer": "count=exact"}
        params = {"select": "id,updated_at", "order": "updated_at.desc,id.desc", "limit": str(limit)}
        try:
            response = await self._client.get(f"{self._settings.supabase_url}/rest/v1/{LIVE_PROJECTS_PATH}", headers=headers, params=params, timeout=self._limits.data_timeout_seconds)
        except httpx.HTTPError:
            return None
        if response.status_code == 401:
            # Usually an expired token: the client refreshes its session and connects again.
            raise Closing(CLOSE_UNAUTHORIZED, "invalid-token")
        if response.status_code not in (200, 206):
            logger.warning("Live look failed: %s", response.status_code)
            return None
        try:
            rows = response.json()
            stamps = {str(row["id"]): str(row["updated_at"]) for row in rows}
        except (ValueError, TypeError, KeyError):
            return None
        total = _total(response.headers.get("Content-Range", ""), len(stamps))
        return Snapshot(stamps=stamps, total=total, complete=total <= len(stamps))

    async def _watch(self, wake: asyncio.Event) -> None:
        known: Snapshot | None = None
        failed = 0
        while True:
            current = await self._look()
            if current is None:
                failed += 1
                if failed >= self._limits.max_failed_looks:
                    raise Closing(CLOSE_TRY_AGAIN_LATER, "projects-unreachable")
            else:
                failed = 0
                if known is None:
                    await self._send({"type": SERVER_READY, "revision": current.revision, "heartbeatSeconds": self._limits.heartbeat_seconds})
                elif current.revision != known.revision:
                    changed = [{"id": key, "updatedAt": stamp} for key, stamp in current.stamps.items() if known.stamps.get(key) != stamp]
                    # A project missing from a partial window may only have left the window, not the account.
                    removed = [key for key in known.stamps if key not in current.stamps] if known.complete and current.complete else []
                    await self._send({"type": SERVER_PROJECTS_CHANGED, "revision": current.revision, "changed": changed, "removed": removed})
                known = current
            try:
                await asyncio.wait_for(wake.wait(), self._settings.live_poll_seconds)
            except TimeoutError:
                pass
            wake.clear()


def offered_subprotocols(websocket: WebSocket) -> list[str]:
    """The subprotocols the client offered. Servers usually parse them into the scope; a host that does not
    still forwards the Sec-WebSocket-Protocol header, so it is read from there too."""
    parsed = list(websocket.scope.get("subprotocols") or [])
    header = websocket.headers.get("sec-websocket-protocol", "")
    return parsed + [value.strip() for value in header.split(",") if value.strip() and value.strip() not in parsed]


def refusal(settings: Settings, origin: str, offered: list[str]) -> str | None:
    """Why a handshake is refused, as a fixed word for the logs; None when it may open."""
    if not settings.live_enabled:
        return "disabled"
    if not settings.identity_enabled:
        return "identity-not-configured"
    if origin not in settings.allowed_origins:
        return "origin"
    if LIVE_SUBPROTOCOL not in offered:
        return "subprotocol"
    return None


@router.websocket(LIVE_PATH)
async def live(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    origin = websocket.headers.get("origin", "").rstrip("/")
    reason = refusal(settings, origin, offered_subprotocols(websocket))
    if reason is not None:
        # The reason only, never the origin or anything else the client sent.
        logger.info("Live handshake refused: %s", reason)
        # Closing before accepting answers the handshake with 403 and opens nothing.
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return
    await websocket.accept(subprotocol=LIVE_SUBPROTOCOL)
    await LiveSession(websocket, settings, websocket.app.state.http_client, websocket.app.state.live_hub).run()
