"""Live channel: handshake rules, first-message authentication, change notices and connection bounds."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings, load_settings
from app.live import LiveHub, Snapshot
from app.live_config import (
    CLOSE_FORBIDDEN, CLOSE_MALFORMED, CLOSE_MESSAGE_TOO_BIG, CLOSE_ROTATE, CLOSE_TIMEOUT, CLOSE_TOO_MANY, CLOSE_TRY_AGAIN_LATER,
    CLOSE_UNAUTHORIZED, CLOSE_UNSUPPORTED_DATA, DEFAULT_LIVE_MAX_CONNECTION_SECONDS, DEFAULT_LIVE_POLL_SECONDS, LIVE_SUBPROTOCOL, LiveLimits,
)
from app.main import create_app

ORIGIN = "https://app.example"
USER = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
FIRST = "00000000-0000-4000-8000-000000000010"
SECOND = "00000000-0000-4000-8000-000000000011"
DATE = "2026-10-12T08:00:00.123456+00:00"
LATER = "2026-10-12T09:00:00.654321+00:00"
WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "source": None, "descriptors": None, "alignment": {"first": "ACD", "second": "ACD", "matrix": "BLOSUM62", "mode": "global", "result": None}}
WRITE = {"name": "Insulin study", "activeSequence": "ACD", "workspace": WORKSPACE}
# A short look interval keeps the tests quick; one test replaces it to prove the wake after a write.
SETTINGS = Settings(allowed_origins=(ORIGIN,), supabase_url="https://project.supabase.co", supabase_anon_key="public-key", live_enabled=True, live_poll_seconds=0.01)
USERS = {"user-token": USER, "fresh-token": USER, "other-token": OTHER}


class Store:
    """The rows the simulated data service returns, and what it was asked."""

    def __init__(self) -> None:
        self.rows: list[dict] = [{"id": FIRST, "updated_at": DATE}]
        self.total: int | None = None
        self.status = 200
        self.tokens: list[str] = []
        self.looks = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        assert request.headers["apikey"] == "public-key"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": USERS[token], "email": "a@example.org"}) if token in USERS else httpx.Response(401, json={})
        if request.url.path == "/rest/v1/projects":
            self.looks += 1
            self.tokens.append(token)
            assert request.url.params["select"] == "id,updated_at"
            assert request.headers["Prefer"] == "count=exact"
            if self.status != 200:
                return httpx.Response(self.status, json={})
            total = len(self.rows) if self.total is None else self.total
            return httpx.Response(200, json=self.rows, headers={"Content-Range": f"0-{max(len(self.rows) - 1, 0)}/{total}"})
        if request.url.path == "/rest/v1/rpc/pf_save_project":
            self.rows = [{"id": SECOND, "updated_at": LATER}, *self.rows]
            return httpx.Response(200, json={**WRITE, "id": SECOND, "status": "draft", "createdAt": LATER, "updatedAt": LATER})
        raise AssertionError(f"Unexpected request: {request.url.path}")


class LiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()

    def client(self, settings: Settings = SETTINGS) -> TestClient:
        return TestClient(create_app(settings, httpx.AsyncClient(transport=httpx.MockTransport(self.store.handler))))

    def connect(self, client: TestClient, origin: str | None = ORIGIN, subprotocols: tuple[str, ...] = (LIVE_SUBPROTOCOL,)):
        return client.websocket_connect("/v1/live", subprotocols=list(subprotocols), headers={"origin": origin} if origin else {})

    def ready(self, socket, token: str = "user-token") -> dict:
        socket.send_json({"type": "auth", "token": token})
        message = socket.receive_json()
        self.assertEqual(message["type"], "ready")
        return message

    def closed(self, socket) -> tuple[int, str]:
        with self.assertRaises(WebSocketDisconnect) as raised:
            while True:
                socket.receive_json()
        return raised.exception.code, raised.exception.reason

    def test_settings_keep_the_channel_off_until_enabled(self):
        self.assertFalse(load_settings({}).live_enabled)
        self.assertFalse(load_settings({"LIVE_ENABLED": "true"}).live_enabled)
        settings = load_settings({"LIVE_ENABLED": "enabled", "LIVE_POLL_SECONDS": "2", "LIVE_MAX_CONNECTION_SECONDS": "60"})
        self.assertEqual((settings.live_enabled, settings.live_poll_seconds, settings.live_max_connection_seconds), (True, 2.0, 60.0))
        self.assertEqual((load_settings({}).live_poll_seconds, load_settings({}).live_max_connection_seconds), (DEFAULT_LIVE_POLL_SECONDS, DEFAULT_LIVE_MAX_CONNECTION_SECONDS))

    def test_handshake_is_refused_when_disabled_or_from_another_origin_or_protocol(self):
        cases = [
            (replace(SETTINGS, live_enabled=False), ORIGIN, (LIVE_SUBPROTOCOL,)),
            (SETTINGS, "https://evil.example", (LIVE_SUBPROTOCOL,)),
            (SETTINGS, None, (LIVE_SUBPROTOCOL,)),
            (SETTINGS, ORIGIN, ()),
            (SETTINGS, ORIGIN, ("other.protocol",)),
            (replace(SETTINGS, supabase_anon_key=""), ORIGIN, (LIVE_SUBPROTOCOL,)),
        ]
        for settings, origin, subprotocols in cases:
            with self.subTest(origin=origin, subprotocols=subprotocols, enabled=settings.live_enabled), self.client(settings) as client:
                with self.assertRaises(WebSocketDisconnect):
                    with self.connect(client, origin, subprotocols):
                        self.fail("The handshake was accepted")
        self.assertEqual(self.store.looks, 0)

    def test_first_message_must_authenticate_before_any_data_is_read(self):
        for first, code, reason in [
            ({"type": "auth", "token": "wrong"}, CLOSE_UNAUTHORIZED, "invalid-token"),
            ({"type": "auth"}, CLOSE_UNAUTHORIZED, "missing-token"),
            ({"type": "ping"}, CLOSE_UNAUTHORIZED, "missing-token"),
            (["auth"], CLOSE_MALFORMED, "malformed"),
        ]:
            with self.subTest(first=first), self.client() as client, self.connect(client) as socket:
                socket.send_json(first)
                self.assertEqual(self.closed(socket), (code, reason))
        self.assertEqual(self.store.looks, 0)

    def test_silent_connection_is_closed_after_the_authentication_window(self):
        settings = replace(SETTINGS, live=LiveLimits(auth_timeout_seconds=0.05))
        with self.client(settings) as client, self.connect(client) as socket:
            self.assertEqual(self.closed(socket), (CLOSE_TIMEOUT, "timeout"))

    def test_ready_then_changes_carry_only_ids_and_timestamps_read_with_the_callers_token(self):
        with self.client() as client, self.connect(client) as socket:
            self.assertEqual(socket.accepted_subprotocol, LIVE_SUBPROTOCOL)
            ready = self.ready(socket)
            self.assertEqual(ready["revision"], Snapshot({FIRST: DATE}, 1, True).revision)
            self.assertEqual(ready["heartbeatSeconds"], SETTINGS.live.heartbeat_seconds)
            self.store.rows = [{"id": SECOND, "updated_at": LATER}, {"id": FIRST, "updated_at": LATER}]
            changed = socket.receive_json()
            self.assertEqual(changed["type"], "projects.changed")
            self.assertEqual(changed["changed"], [{"id": SECOND, "updatedAt": LATER}, {"id": FIRST, "updatedAt": LATER}])
            self.assertEqual(changed["removed"], [])
            self.assertNotEqual(changed["revision"], ready["revision"])
            self.store.rows = [{"id": SECOND, "updated_at": LATER}]
            removed = socket.receive_json()
            self.assertEqual((removed["changed"], removed["removed"]), ([], [FIRST]))
        self.assertEqual(set(self.store.tokens), {"user-token"})

    def test_partial_window_reports_the_count_change_without_claiming_a_removal(self):
        self.store.total = 500
        with self.client() as client, self.connect(client) as socket:
            self.ready(socket)
            self.store.rows, self.store.total = [{"id": SECOND, "updated_at": LATER}], 499
            message = socket.receive_json()
            self.assertEqual((message["type"], message["removed"]), ("projects.changed", []))

    def test_write_in_this_process_wakes_the_owners_connections_at_once(self):
        # With a look every hour, only the wake after the write can deliver the notice.
        with self.client(replace(SETTINGS, live_poll_seconds=3600.0)) as client, self.connect(client) as socket:
            self.ready(socket)
            created = client.post("/v1/projects", headers={"Authorization": "Bearer user-token"}, json=WRITE)
            self.assertEqual(created.status_code, 201)
            message = socket.receive_json()
            self.assertEqual(message["changed"], [{"id": SECOND, "updatedAt": LATER}])

    def test_ping_is_answered_and_a_refreshed_token_replaces_the_old_one(self):
        with self.client() as client, self.connect(client) as socket:
            self.ready(socket)
            socket.send_json({"type": "ping"})
            self.assertEqual(socket.receive_json(), {"type": "pong"})
            socket.send_json({"type": "auth", "token": "fresh-token"})
            self.store.rows = []
            self.assertEqual(socket.receive_json()["removed"], [FIRST])
            self.assertEqual(self.store.tokens[-1], "fresh-token")

    def test_connection_cannot_move_to_another_account(self):
        with self.client() as client, self.connect(client) as socket:
            self.ready(socket)
            socket.send_json({"type": "auth", "token": "other-token"})
            self.assertEqual(self.closed(socket), (CLOSE_FORBIDDEN, "identity-changed"))

    def test_messages_outside_the_protocol_close_the_connection(self):
        def binary(socket): socket.send_bytes(b"\x00")
        def huge(socket): socket.send_text(json.dumps({"type": "ping", "pad": "x" * SETTINGS.live.max_message_bytes}))
        def unknown(socket): socket.send_json({"type": "subscribe"})
        def text(socket): socket.send_text("not json")
        for send, code in [(binary, CLOSE_UNSUPPORTED_DATA), (huge, CLOSE_MESSAGE_TOO_BIG), (unknown, CLOSE_MALFORMED), (text, CLOSE_MALFORMED)]:
            with self.subTest(code=code), self.client() as client, self.connect(client) as socket:
                self.ready(socket)
                send(socket)
                self.assertEqual(self.closed(socket)[0], code)

    def test_message_budget_and_connections_per_account_are_bounded(self):
        settings = replace(SETTINGS, live=LiveLimits(messages_per_minute=3, connections_per_caller=1))
        with self.client(settings) as client, self.connect(client) as socket:
            self.ready(socket)
            with self.connect(client) as second:
                second.send_json({"type": "auth", "token": "user-token"})
                self.assertEqual(self.closed(second), (CLOSE_TOO_MANY, "too-many-connections"))
            with self.connect(client) as other:
                self.ready(other, "other-token")
            for _ in range(3):
                socket.send_json({"type": "ping"})
            self.assertEqual(self.closed(socket), (CLOSE_TOO_MANY, "too-many-messages"))
            self.assertEqual(client.app.state.live_hub.connections(USER), 0)

    def test_server_rotates_the_connection_before_the_platform_limit(self):
        with self.client(replace(SETTINGS, live_max_connection_seconds=0.05)) as client, self.connect(client) as socket:
            self.ready(socket)
            self.assertEqual(self.closed(socket), (CLOSE_ROTATE, "rotate"))

    def test_expired_token_and_unreachable_data_hand_the_connection_back(self):
        with self.client() as client, self.connect(client) as socket:
            self.ready(socket)
            self.store.status = 401
            self.assertEqual(self.closed(socket), (CLOSE_UNAUTHORIZED, "invalid-token"))
        self.store.status = 200
        with self.client() as client, self.connect(client) as socket:
            self.ready(socket)
            self.store.status = 503
            self.assertEqual(self.closed(socket), (CLOSE_TRY_AGAIN_LATER, "projects-unreachable"))
        self.assertGreaterEqual(self.store.looks, SETTINGS.live.max_failed_looks)


class LiveHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_reaches_only_the_owner_and_leaving_frees_the_place(self):
        hub = LiveHub(connections_per_caller=2)
        mine, also_mine, theirs = hub.join(USER), hub.join(USER), hub.join(OTHER)
        self.assertIsNone(hub.join(USER))
        hub.notify(USER)
        hub.notify(None)
        self.assertTrue(mine.is_set() and also_mine.is_set())
        self.assertFalse(theirs.is_set())
        hub.leave(USER, mine)
        hub.leave(USER, also_mine)
        hub.leave(USER, also_mine)
        self.assertEqual(hub.connections(USER), 0)
        self.assertIsNotNone(hub.join(USER))


if __name__ == "__main__":
    unittest.main()
