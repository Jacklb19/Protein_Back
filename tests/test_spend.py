"""Shared budget through pf_spend with a simulated database; the real SQL and its concurrency live in test_postgres.py."""
import json
import re
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app import spend_config
from app.config import Settings, load_settings
from app.main import create_app

USER = "00000000-0000-4000-8000-000000000001"
TOKEN = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
ASSISTANT = "/proteinforge.assistant.v1.AssistantService"
BODY = {"subject": "laboratory", "language": "es", "facts": [{"label": "pLDDT medio", "value": "93,39", "unit": "/ 100"}], "sequence": "MQIFVK"}
ANSWER = "La variante tiene un pLDDT medio de 93,39 sobre 100."
SETTINGS = Settings(
    supabase_url="https://project.supabase.co", supabase_anon_key="public-key", groq_api_key="gsk_test", allowed_origins=("http://127.0.0.1:5173",),
    assistant_requests_per_minute=2, uniprot_requests_per_minute=3, shared_budget_enabled=True, assistant_cache_max_entries=0,
)
MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261113000001_shared_budget.sql"
UNIPROT_SEARCH = {"results": []}


class Ledger:
    """What pf_spend and pf_spend_remaining would answer, with a switch to make them fail."""

    def __init__(self) -> None:
        self.used: dict[str, int] = {}
        self.calls: list[tuple[str, dict, str]] = []
        self.failure: httpx.Response | Exception | None = None
        self.allow_next: bool | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/rest/v1/")
        body = json.loads(request.content)
        self.calls.append((path, body, request.headers.get("Authorization", "")))
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        limit = body["p_limit"]
        used = self.used.get(body["p_scope"], 0)
        if path == "rpc/pf_spend_remaining":
            return httpx.Response(200, json=limit - used)
        if used >= limit or self.allow_next is False:
            return httpx.Response(200, json={"allowed": False, "remaining": limit - used})
        self.used[body["p_scope"]] = used + 1
        return httpx.Response(200, json={"allowed": True, "remaining": limit - used - 1})


class SpendTests(unittest.TestCase):
    def client(self, settings: Settings = SETTINGS):
        self.ledger = Ledger()
        self.provider_calls = 0

        def transport(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            if request.url.path.startswith("/rest/v1/rpc/pf_spend"):
                self.assertEqual(request.headers["apikey"], "public-key")
                return self.ledger.handle(request)
            self.provider_calls += 1
            if request.url.host == "api.groq.com":
                return httpx.Response(200, json={"choices": [{"message": {"content": ANSWER}, "finish_reason": "stop"}]})
            if request.url.host == "rest.uniprot.org":
                return httpx.Response(200, json=UNIPROT_SEARCH)
            raise AssertionError(f"Unexpected request: {request.url}")

        return TestClient(create_app(settings, httpx.AsyncClient(transport=httpx.MockTransport(transport))))

    def interpret(self, client, body=BODY):
        return client.post("/v1/assistant/interpret", json=body, headers=TOKEN)

    def test_settings_keep_the_shared_budget_off_until_enabled(self):
        self.assertFalse(load_settings({}).shared_budget_enabled)
        self.assertFalse(load_settings({"SHARED_BUDGET_ENABLED": "true"}).shared_budget_enabled)
        settings = load_settings({"SHARED_BUDGET_ENABLED": "enabled", "ASSISTANT_REQUESTS_PER_DAY": "200"})
        self.assertEqual((settings.shared_budget_enabled, settings.assistant_requests_per_day), (True, 200))
        self.assertEqual(load_settings({}).assistant_requests_per_day, 0)

    def test_the_database_counts_the_assistant_with_the_callers_token(self):
        with self.client(replace(SETTINGS, assistant_requests_per_day=500)) as client:
            first, second, third = (self.interpret(client) for _ in range(3))
        self.assertEqual([first.status_code, second.status_code, third.status_code], [200, 200, 429])
        self.assertEqual([first.headers["X-Assistant-Remaining"], second.headers["X-Assistant-Remaining"]], ["1", "0"])
        self.assertEqual(self.provider_calls, 2)
        path, body, authorization = self.ledger.calls[0]
        self.assertEqual(path, "rpc/pf_spend")
        self.assertEqual(body, {"p_scope": "assistant", "p_limit": 2, "p_window_seconds": 60, "p_global_limit": 500, "p_global_window_seconds": 86400})
        self.assertEqual(authorization, "Bearer user-token")

    def test_a_cached_reading_spends_nothing_and_still_reports_what_is_left(self):
        with self.client(replace(SETTINGS, assistant_cache_max_entries=8)) as client:
            first = self.interpret(client)
            again = self.interpret(client)
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual([call[0] for call in self.ledger.calls], ["rpc/pf_spend", "rpc/pf_spend_remaining"])
        self.assertEqual((first.headers["X-Assistant-Remaining"], again.headers["X-Assistant-Remaining"]), ("1", "1"))
        self.assertEqual(self.provider_calls, 1)

    def test_invalid_requests_never_reach_the_budget(self):
        with self.client() as client:
            self.assertEqual(self.interpret(client, {**BODY, "facts": []}).status_code, 422)
            self.assertEqual(client.get("/v1/uniprot/search", params={"query": "a"}, headers=TOKEN).status_code, 422)
            self.assertEqual(client.get("/v1/uniprot/not-an-accession", headers=TOKEN).status_code, 422)
        self.assertEqual(self.ledger.calls, [])

    def test_rpc_reports_the_database_remaining_count(self):
        with self.client() as client:
            response = client.post(f"{ASSISTANT}/Interpret", headers={**CONNECT, **TOKEN}, content=json.dumps({"subject": "SUBJECT_LABORATORY", "language": "es", "facts": BODY["facts"], "sequence": "MQIFVK"}))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["X-Assistant-Remaining"], "1")

    def test_uniprot_spends_its_own_scope(self):
        with self.client() as client:
            codes = [client.get("/v1/uniprot/search", params={"query": "insulin"}, headers=TOKEN).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        self.assertEqual({call[1]["p_scope"] for call in self.ledger.calls}, {"uniprot"})
        self.assertEqual(self.ledger.calls[0][1]["p_global_limit"], 0)
        self.assertEqual(self.provider_calls, 3)

    def test_an_unreachable_or_unmigrated_database_falls_back_to_the_process_counters(self):
        failures = [
            httpx.Response(404, json={"code": "PGRST202", "message": "Could not find the function"}),
            httpx.Response(500, json={"code": "XX000"}),
            httpx.ConnectError("down"),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json={"allowed": "yes"}),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), self.client() as client:
                self.ledger.failure = failure
                with self.assertLogs("app.spend", level="WARNING") as logs:
                    codes = [self.interpret(client).status_code for _ in range(3)]
                self.assertEqual(codes, [200, 200, 429])
                self.assertTrue(all("user-token" not in line and USER not in line for line in logs.output))

    def test_an_invalid_token_is_never_a_fallback(self):
        with self.client() as client:
            self.ledger.failure = httpx.Response(401, json={"code": "PT401", "message": "Authentication required"})
            self.assertEqual(self.interpret(client).status_code, 401)
        self.assertEqual(self.provider_calls, 0)

    def test_the_daily_cap_also_holds_in_the_process_fallback(self):
        with self.client(replace(SETTINGS, assistant_requests_per_minute=0, assistant_requests_per_day=2)) as client:
            self.ledger.failure = httpx.ConnectError("down")
            with self.assertLogs("app.spend", level="WARNING"):
                responses = [self.interpret(client) for _ in range(3)]
        self.assertEqual([response.status_code for response in responses], [200, 200, 429])
        self.assertNotIn("X-Assistant-Remaining", responses[0].headers)

    def test_without_the_setting_the_database_is_never_asked(self):
        with self.client(replace(SETTINGS, shared_budget_enabled=False)) as client:
            codes = [self.interpret(client).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(self.ledger.calls, [])

    def test_ceilings_clamp_the_limit_sent_and_match_the_migration(self):
        with self.client(replace(SETTINGS, assistant_requests_per_minute=500)) as client:
            response = self.interpret(client)
        self.assertEqual(self.ledger.calls[0][1]["p_limit"], spend_config.SPEND_CEILINGS["assistant"])
        self.assertEqual(response.headers["X-Assistant-Remaining"], str(spend_config.SPEND_CEILINGS["assistant"] - 1))
        sql = MIGRATION.read_text(encoding="utf-8")
        expected = " ".join(f"when '{scope}' then {ceiling}" for scope, ceiling in spend_config.SPEND_CEILINGS.items())
        self.assertEqual(sql.count(expected), 2)
        self.assertIn(f"p_global_limit not between 0 and {spend_config.GLOBAL_SPEND_CEILING}", sql)
        self.assertEqual(len(re.findall(rf"between 1 and {spend_config.DAY_SECONDS}", sql)), 3)
        self.assertIn("revoke all on public.spend_windows from public, anon, authenticated", sql)


class FoldingSpendTests(unittest.TestCase):
    def test_prediction_spends_in_the_database_and_releases_the_gate_when_refused(self):
        import asyncio

        from fastapi import HTTPException

        from app.assistant_cache import AssistantCache
        from app.folding import FoldingGate, predict
        from app.folding_models import FoldRequest
        from app.live import LiveHub
        from app.rate_limit import FixedWindowLimiter
        from app.sifts_cache import SiftsCache

        calls = []

        def transport(request: httpx.Request) -> httpx.Response:
            calls.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"allowed": False, "remaining": 0})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http_client:
                state = SimpleNamespace(
                    settings=replace(SETTINGS, folding_enabled=True), http_client=http_client, folding_gate=FoldingGate(),
                    uniprot_limiter=FixedWindowLimiter(0), assistant_limiter=FixedWindowLimiter(0), assistant_daily_limiter=FixedWindowLimiter(0),
                    shared_limiter=FixedWindowLimiter(0), sifts_cache=SiftsCache(), assistant_cache=AssistantCache(0, 0), live_hub=LiveHub(1),
                )
                with self.assertRaises(HTTPException) as raised:
                    await predict(state, FoldRequest(sequence="MQIFVKTLTG", consent_to_send_sequence=True), USER, "user-token")
                return state, raised.exception
        state, error = asyncio.run(run())
        self.assertEqual((error.status_code, error.detail), (429, "folding-rate-limit"))
        self.assertFalse(state.folding_gate._active)
        self.assertEqual(calls, [("/rest/v1/rpc/pf_spend", {"p_scope": "folding", "p_limit": 1, "p_window_seconds": 60, "p_global_limit": 3, "p_global_window_seconds": 60})])


if __name__ == "__main__":
    unittest.main()
