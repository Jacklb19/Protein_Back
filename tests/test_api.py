"""Routes, problem details and identity validation with a simulated Supabase Auth."""

from __future__ import annotations

import json
import unittest

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app

SETTINGS = Settings(allowed_origins=("http://localhost:5173",), supabase_url="https://project.supabase.co", supabase_anon_key="public-key")


def fake_supabase(handler):
    """Builds an async client whose transport answers as the Supabase user endpoint would."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def user_endpoint(request: httpx.Request) -> httpx.Response:
    assert request.url == "https://project.supabase.co/auth/v1/user"
    assert request.headers["apikey"] == "public-key"
    if request.headers.get("Authorization") == "Bearer valid-token":
        return httpx.Response(200, json={"id": "user-1", "email": "ana@example.org"})
    return httpx.Response(401, json={"msg": "invalid JWT"})


class SettingsTests(unittest.TestCase):
    def test_reads_origins_and_identity_from_environment(self):
        settings = load_settings({"ALLOWED_ORIGINS": "https://a.example/, https://b.example", "SUPABASE_URL": "https://p.supabase.co/", "SUPABASE_ANON_KEY": " key "})
        self.assertEqual(settings.allowed_origins, ("https://a.example", "https://b.example"))
        self.assertTrue(settings.identity_enabled)
        self.assertEqual(settings.identity_user_url, "https://p.supabase.co/auth/v1/user")

    def test_falls_back_to_frontend_origin_and_disables_identity(self):
        settings = load_settings({"FRONTEND_ORIGIN": "http://localhost:5173"})
        self.assertEqual(settings.allowed_origins, ("http://localhost:5173",))
        self.assertFalse(settings.identity_enabled)


class ApiTests(unittest.TestCase):
    def client(self, handler=user_endpoint, settings: Settings = SETTINGS) -> TestClient:
        return TestClient(create_app(settings, fake_supabase(handler)))

    def test_health_reports_version_and_identity_configuration(self):
        with self.client() as client:
            response = client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": "0.1.0", "identity": True})
        self.assertTrue(response.headers["X-Request-Id"])

    def test_cors_allows_only_configured_origins(self):
        with self.client() as client:
            allowed = client.options("/v1/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
            denied = client.options("/v1/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:5173")
        self.assertIsNone(denied.headers.get("access-control-allow-origin"))

    def test_me_requires_a_bearer_token_and_returns_problem_details(self):
        with self.client() as client:
            response = client.get("/v1/me", headers={"X-Request-Id": "trace-123"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        body = response.json()
        self.assertEqual(body["status"], 401)
        self.assertEqual(body["requestId"], "trace-123")
        self.assertEqual(body["instance"], "/v1/me")

    def test_me_resolves_the_identity_through_supabase(self):
        with self.client() as client:
            valid = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})
            invalid = client.get("/v1/me", headers={"Authorization": "Bearer other-token"})
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json(), {"id": "user-1", "email": "ana@example.org"})
        self.assertEqual(invalid.status_code, 401)
        self.assertIn("invalid-token", invalid.json()["type"])

    def test_me_reports_unavailable_identity_without_configuration(self):
        with self.client(settings=Settings()) as client:
            response = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})
        self.assertEqual(response.status_code, 503)

    def test_upstream_failures_become_gateway_errors_without_leaking_details(self):
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        def broken(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"<html>secret stack trace</html>")

        with self.client(timeout) as client:
            slow = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})
        with self.client(broken) as client:
            failed = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})
        self.assertEqual(slow.status_code, 504)
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("stack trace", json.dumps(failed.json()))


if __name__ == "__main__":
    unittest.main()
