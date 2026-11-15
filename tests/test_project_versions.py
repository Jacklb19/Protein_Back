"""Project versions over REST and RPC with a simulated Supabase transport; SQL behaviour lives in test_postgres.py."""
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
VERSION = "00000000-0000-4000-8000-000000000020"
DATE = "2026-10-12T08:00:00.123456+00:00"
LATER = "2026-10-12T09:00:00.654321+00:00"
WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "source": None, "descriptors": None, "alignment": {"first": "", "second": "", "matrix": "BLOSUM62", "mode": "global", "result": None}}
DOCUMENT = {"name": "Insulin study", "activeSequence": "ACDE", "workspace": WORKSPACE, "id": PROJECT, "status": "draft", "createdAt": DATE, "updatedAt": LATER}
PREVIEW = {"sequenceLength": 4, "sequencePrefix": "ACDE", "excluded": 0, "descriptors": None, "alignment": None, "source": None}
ROW = {"id": VERSION, "savedAt": DATE, "preview": PREVIEW}
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key")
TOKEN = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
PROJECTS = "/proteinforge.project.v1.ProjectService"
MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261110000000_project_versions.sql"


class ProjectVersionTests(unittest.TestCase):
    def client(self, data):
        self.sent = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer user-token")
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            self.sent.append((request.url.path, json.loads(request.content)))
            return data(request)
        return TestClient(create_app(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    def test_versions_require_identity_before_any_data_request(self):
        with self.client(lambda _: self.fail("Unauthenticated data request")) as client:
            self.assertEqual(client.get(f"/v1/projects/{PROJECT}/versions").status_code, 401)
            self.assertEqual(client.post(f"/v1/projects/{PROJECT}/versions/{VERSION}/restore", json={"expectedUpdatedAt": DATE}).status_code, 401)

    def test_list_forwards_the_project_and_returns_compact_previews(self):
        with self.client(lambda _: httpx.Response(200, json=[ROW])) as client:
            response = client.get(f"/v1/projects/{PROJECT}/versions", headers=TOKEN)
            invalid = client.get("/v1/projects/not-a-uuid/versions", headers=TOKEN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], VERSION)
        self.assertEqual(response.json()["items"][0]["preview"]["sequenceLength"], 4)
        self.assertEqual(self.sent, [("/rest/v1/rpc/pf_list_project_versions_compared", {"p_id": PROJECT, "p_prefix_length": project_config.PROJECT_PREVIEW_RESIDUES})])
        self.assertEqual(invalid.status_code, 422)

    def test_a_listing_beyond_the_limit_or_without_its_fields_is_refused(self):
        too_many = [{**ROW, "id": f"00000000-0000-4000-8000-0000000000{30 + index}"} for index in range(project_config.PROJECT_VERSION_LIMIT + 1)]
        for rows in (too_many, [{"savedAt": DATE}], {"not": "a list"}):
            with self.subTest(rows=str(rows)[:40]), self.client(lambda _, rows=rows: httpx.Response(200, json=rows)) as client:
                self.assertEqual(client.get(f"/v1/projects/{PROJECT}/versions", headers=TOKEN).status_code, 502)

    def test_restore_forwards_both_ids_and_the_expected_version(self):
        with self.client(lambda _: httpx.Response(200, json=DOCUMENT)) as client:
            response = client.post(f"/v1/projects/{PROJECT}/versions/{VERSION}/restore", headers=TOKEN, json={"expectedUpdatedAt": DATE})
            naive = client.post(f"/v1/projects/{PROJECT}/versions/{VERSION}/restore", headers=TOKEN, json={"expectedUpdatedAt": "2026-10-12T08:00:00"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["activeSequence"], "ACDE")
        self.assertEqual(self.sent[0], ("/rest/v1/rpc/pf_restore_project_version", {"p_project_id": PROJECT, "p_version_id": VERSION, "p_expected_updated_at": DATE}))
        self.assertEqual(naive.status_code, 422)

    def test_restore_names_a_missing_version_and_a_stale_project(self):
        cases = [
            ({"code": "PT404", "message": "Version not found"}, 404, "project-version-not-found"),
            ({"code": "PT409", "message": "Project changed"}, 409, "project-conflict"),
        ]
        for body, status, detail in cases:
            with self.subTest(detail=detail), self.client(lambda _, body=body: httpx.Response(400, json=body)) as client:
                response = client.post(f"/v1/projects/{PROJECT}/versions/{VERSION}/restore", headers=TOKEN, json={"expectedUpdatedAt": DATE})
            self.assertEqual(response.status_code, status)
            self.assertIn(detail, response.text)

    def test_rpc_lists_and_restores_like_rest(self):
        def data(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[ROW] if request.url.path.endswith("pf_list_project_versions_compared") else DOCUMENT)
        with self.client(data) as client:
            listed = client.post(f"{PROJECTS}/ListProjectVersions", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT}))
            restored = client.post(f"{PROJECTS}/RestoreProjectVersion", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT, "versionId": VERSION, "expectedUpdateTime": "2026-10-12T08:00:00.123456Z"}))
            invalid = client.post(f"{PROJECTS}/RestoreProjectVersion", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT, "versionId": "nope"}))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["versions"][0]["id"], VERSION)
        self.assertEqual(listed.json()["versions"][0]["preview"]["sequenceLength"], 4)
        self.assertTrue(listed.json()["versions"][0]["saveTime"].startswith("2026-10-12T08:00:00.123456"))
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["project"]["activeSequence"], "ACDE")
        self.assertEqual(self.sent[-1][1]["p_expected_updated_at"], DATE)
        self.assertEqual(invalid.json()["code"], "invalid_argument")

    def test_migration_keeps_the_limit_of_the_code_and_grants_owners_reading_only(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        limit = re.search(r"limit (\d+) -- PROJECT_VERSION_LIMIT", sql)
        self.assertIsNotNone(limit)
        self.assertEqual(int(limit.group(1)), project_config.PROJECT_VERSION_LIMIT)
        self.assertIn("grant select on public.project_versions to authenticated;", sql)
        self.assertNotRegex(sql, r"grant\s+(insert|update|delete)[^;]*project_versions")
        self.assertIn("security definer", sql.split("function public.pf_keep_project_version")[1].split("$$")[0])


    def test_each_version_says_whether_its_sequence_is_the_current_one(self):
        rows = [{**ROW, "sameSequence": True}, {**ROW, "id": "00000000-0000-4000-8000-000000000021", "sameSequence": False}, {**ROW, "id": "00000000-0000-4000-8000-000000000022"}]
        with self.client(lambda _: httpx.Response(200, json=rows)) as client:
            rest = client.get(f"/v1/projects/{PROJECT}/versions", headers=TOKEN).json()["items"]
            rpc = client.post(f"{PROJECTS}/ListProjectVersions", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT})).json()["versions"]
        self.assertEqual([item["sameSequence"] for item in rest], [True, False, None])
        # Unset stays unset over RPC, so a client can tell an older server from a different sequence.
        self.assertEqual([item.get("sameSequence") for item in rpc], [True, False, None])

    def test_the_comparison_reads_the_full_sequences_under_the_owner(self):
        sql = (MIGRATION.parent / "20261114000001_version_changes.sql").read_text(encoding="utf-8")
        self.assertIn("'sameSequence', v.active_sequence = p.active_sequence", sql)
        self.assertIn("security invoker", sql)
        self.assertIn("v.owner_id = (select auth.uid())", sql)
        self.assertNotIn("to anon", sql)

if __name__ == "__main__":
    unittest.main()
