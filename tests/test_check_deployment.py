"""Scheduled deployment check: decisions against the real application with a simulated database."""
import importlib.util
import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import project_config
from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_deployment", ROOT / "scripts/check_deployment.py")
check_deployment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_deployment
SPEC.loader.exec_module(check_deployment)
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", allowed_origins=("http://127.0.0.1:5173",))
BASE = "http://testserver"


class DeploymentCheckTests(unittest.TestCase):
    def run_against(self, database, settings: Settings = SETTINGS) -> list[str]:
        self.database_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.database_calls.append(request.url.path)
            self.assertNotIn("Authorization", request.headers)
            return database(request)

        with TestClient(create_app(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))) as client:
            def call(url: str, method: str, payload):
                response = client.request(method, url.removeprefix(BASE), json=payload)
                try:
                    body = response.json()
                except ValueError:
                    body = None
                return check_deployment.Answer(response.status_code, body)
            return check_deployment.check(BASE + "/", call)

    def test_a_healthy_deployment_passes_and_the_lookup_reaches_the_database(self):
        self.assertEqual(self.run_against(lambda _: httpx.Response(200, content=b"null")), [])
        self.assertEqual(self.database_calls, ["/rest/v1/rpc/pf_shared_project"])

    def test_an_unreachable_or_paused_database_fails_the_check(self):
        for status in (500, 503, 404):
            with self.subTest(status=status):
                problems = self.run_against(lambda _, status=status: httpx.Response(status, json={"message": "paused"}))
                self.assertEqual(len(problems), 1)
                self.assertIn("shared lookup answered", problems[0])

    def test_missing_identity_configuration_fails_the_health_part(self):
        problems = self.run_against(lambda _: httpx.Response(200, content=b"null"), replace(SETTINGS, supabase_anon_key=""))
        self.assertIn("health reports identity is not configured", problems)

    def test_the_invented_token_has_the_shape_of_a_real_link(self):
        sent = []
        check_deployment.check(BASE, lambda url, method, payload: sent.append(payload) or check_deployment.Answer(404, {"type": "x/shared-not-found", "status": "ok", "identity": True}))
        self.assertRegex(sent[1]["token"], f"^{project_config.PROJECT_SHARE_TOKEN_PATTERN}$")
        self.assertEqual(check_deployment.SHARED_TOKEN_BYTES, project_config.PROJECT_SHARE_TOKEN_BYTES)

    def test_the_workflow_runs_every_six_hours_against_production_without_secrets(self):
        workflow = (ROOT / ".github/workflows/deployment-check.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '17 */6 * * *'", workflow)
        self.assertIn("python scripts/check_deployment.py https://protein-back.vercel.app", workflow)
        self.assertIsNone(re.search(r"secrets\.", workflow))


if __name__ == "__main__":
    unittest.main()
