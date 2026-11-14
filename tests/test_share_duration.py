"""Link duration and the listing's link expiry over REST and RPC; the SQL side lives in test_postgres.py."""
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
DATE = "2026-11-14T08:00:00.123456+00:00"
LATER = "2026-12-14T08:00:00.123456+00:00"
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", allowed_origins=("http://127.0.0.1:5173",))
TOKEN = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
PROJECTS = "/proteinforge.project.v1.ProjectService"
MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261114000000_share_duration.sql"
ROW = {"id": PROJECT, "name": "Insulin study", "status": "draft", "created_at": DATE, "updated_at": DATE, "preview": None}


class ShareDurationTests(unittest.TestCase):
    def client(self, data):
        self.sent = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            self.sent.append((request.url.path, json.loads(request.content)))
            return data(request)
        return TestClient(create_app(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    def created(self, _request):
        return httpx.Response(200, json={"createdAt": DATE, "expiresAt": LATER})

    def test_rest_sends_the_chosen_duration_and_keeps_seven_days_without_one(self):
        with self.client(self.created) as client:
            chosen = client.post(f"/v1/projects/{PROJECT}/share", headers=TOKEN, json={"durationDays": 30})
            empty = client.post(f"/v1/projects/{PROJECT}/share", headers=TOKEN)
        self.assertEqual((chosen.status_code, empty.status_code), (201, 201))
        self.assertEqual([body["p_days"] for _, body in self.sent], [30, project_config.PROJECT_SHARE_LIFETIME_DAYS])

    def test_rest_refuses_a_duration_that_is_not_offered_before_any_data_request(self):
        with self.client(self.created) as client:
            codes = [client.post(f"/v1/projects/{PROJECT}/share", headers=TOKEN, json={"durationDays": days}).status_code for days in (0, 2, 31, 365, -7)]
        self.assertEqual(codes, [422] * 5)
        self.assertEqual(self.sent, [])

    def test_rpc_takes_the_duration_and_treats_unset_as_the_default(self):
        with self.client(self.created) as client:
            one = client.post(f"{PROJECTS}/CreateProjectShare", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT, "durationDays": 1}))
            unset = client.post(f"{PROJECTS}/CreateProjectShare", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT}))
            wrong = client.post(f"{PROJECTS}/CreateProjectShare", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT, "durationDays": 14}))
        self.assertEqual((one.status_code, unset.status_code), (200, 200))
        self.assertEqual([body["p_days"] for _, body in self.sent], [1, project_config.PROJECT_SHARE_LIFETIME_DAYS])
        self.assertEqual(wrong.json()["code"], "invalid_argument")

    def test_listing_carries_the_link_expiry_only_when_there_is_one(self):
        rows = [{**ROW, "share_expires_at": LATER}, {**ROW, "id": "00000000-0000-4000-8000-000000000011", "share_expires_at": None}]
        with self.client(lambda _: httpx.Response(200, json=rows)) as client:
            rest = client.get("/v1/projects", headers=TOKEN).json()["items"]
            rpc = client.post(f"{PROJECTS}/ListProjects", headers={**CONNECT, **TOKEN}, content=json.dumps({})).json()["projects"]
        self.assertEqual(rest[0]["shareExpiresAt"], LATER)
        self.assertIsNone(rest[1]["shareExpiresAt"])
        self.assertTrue(rpc[0]["shareExpireTime"].startswith("2026-12-14T08:00:00.123456"))
        self.assertNotIn("shareExpireTime", rpc[1])
        self.assertEqual(self.sent[0][0], f"/rest/v1/{project_config.PROJECT_LIST_RPC}")

    def test_durations_match_the_migration_and_older_clients_keep_seven_days(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        offered = re.search(r"p_days not in \(([\d, ]+)\) then -- PROJECT_SHARE_DURATIONS_DAYS", sql)
        self.assertIsNotNone(offered)
        self.assertEqual(tuple(int(day) for day in offered.group(1).split(",")), project_config.PROJECT_SHARE_DURATIONS_DAYS)
        self.assertIn(f"pf_share_project(p_id, p_token_sha256, {project_config.PROJECT_SHARE_LIFETIME_DAYS}); -- PROJECT_SHARE_LIFETIME_DAYS", sql)
        self.assertIn(project_config.PROJECT_SHARE_LIFETIME_DAYS, project_config.PROJECT_SHARE_DURATIONS_DAYS)
        self.assertEqual(re.findall(r"to anon", sql), [])


if __name__ == "__main__":
    unittest.main()
