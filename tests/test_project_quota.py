"""Per-account project quota as REST and RPC see it; the SQL count lives in test_postgres.py."""
import json
import re
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import project_config
from app.config import Settings
from app.main import create_app

USER = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000010"
DATE = "2026-10-12T08:00:00.123456+00:00"
WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "source": None, "descriptors": None, "alignment": {"first": "ACD", "second": "ACD", "matrix": "BLOSUM62", "mode": "global", "result": None}}
WRITE = {"name": "Insulin study", "activeSequence": "ACD", "workspace": WORKSPACE}
RPC_WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "alignment": {"first": "ACD", "second": "ACD", "matrix": "SUBSTITUTION_MATRIX_BLOSUM62", "mode": "ALIGNMENT_MODE_GLOBAL"}}
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", allowed_origins=("http://127.0.0.1:5173",))
HEADERS = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
PROJECTS = "/proteinforge.project.v1.ProjectService"
MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261113000000_project_quota.sql"
QUOTA_REFUSAL = {"code": "PT413", "message": project_config.PROJECT_QUOTA_MESSAGE, "details": None, "hint": None}


class ProjectQuotaTests(unittest.TestCase):
    def client(self, data):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            return data(request)
        return TestClient(create_app(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    def test_rest_create_refused_by_the_quota_has_its_own_code(self):
        with self.client(lambda _: httpx.Response(400, json=QUOTA_REFUSAL)) as client:
            response = client.post("/v1/projects", headers=HEADERS, json=WRITE)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["type"].rsplit("/", 1)[-1], "project-quota-exceeded")
        self.assertNotIn("Insulin", response.text)

    def test_rpc_create_refused_by_the_quota_is_resource_exhausted_with_its_own_code(self):
        with self.client(lambda _: httpx.Response(400, json=QUOTA_REFUSAL)) as client:
            response = client.post(f"{PROJECTS}/CreateProject", headers={**CONNECT, **HEADERS}, content=json.dumps({"name": "x", "activeSequence": "ACD", "workspace": RPC_WORKSPACE}))
        self.assertEqual(response.json(), {"code": "resource_exhausted", "message": "project-quota-exceeded"})

    def test_other_pt413_messages_are_not_taken_for_the_quota(self):
        with self.client(lambda _: httpx.Response(400, json={**QUOTA_REFUSAL, "message": "Something else"})) as client:
            response = client.post("/v1/projects", headers=HEADERS, json=WRITE)
        self.assertEqual(response.status_code, 502)

    def test_updates_are_never_refused_by_the_quota_path(self):
        # The quota is an insert trigger: an update request never reaches it, whatever the count.
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("before insert on public.projects", sql)
        self.assertNotRegex(sql, r"before (insert or )?update")

    def test_limit_and_message_match_the_migration(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertEqual(project_config.PROJECT_LIMIT, 50)
        self.assertRegex(sql, rf"stored_count >= {project_config.PROJECT_LIMIT}\b")
        self.assertIn(f"message = '{project_config.PROJECT_QUOTA_MESSAGE}'", sql)
        self.assertIn("PROJECT_LIMIT in app/project_config.py", sql)
        # Idempotent and serialized per owner, like the report quota.
        self.assertIn("drop trigger if exists pf_project_quota", sql)
        self.assertIsNotNone(re.search(r"pg_advisory_xact_lock\(hashtextextended\('pf_projects:'", sql))


if __name__ == "__main__":
    unittest.main()
