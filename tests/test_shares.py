"""Read-only links over REST and RPC with a simulated Supabase transport; SQL behaviour lives in test_postgres.py."""
import hashlib
import json
import re
import unittest
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import project_config
from app.config import Settings, load_settings
from app.main import create_app

USER = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000010"
DATE = "2026-11-10T08:00:00.123456+00:00"
LATER = "2026-11-17T08:00:00.123456+00:00"
WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "source": None, "descriptors": None, "alignment": {"first": "", "second": "", "matrix": "BLOSUM62", "mode": "global", "result": None}}
SHARED = {"name": "Insulin study", "activeSequence": "ACDE", "workspace": WORKSPACE, "updatedAt": DATE, "expiresAt": LATER}
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", allowed_origins=("http://127.0.0.1:5173",))
TOKEN = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
PROJECTS = "/proteinforge.project.v1.ProjectService"
LINK = "A" * 43
MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261111000000_project_shares.sql"


class ShareTests(unittest.TestCase):
    def client(self, data, settings: Settings = SETTINGS):
        self.sent = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/v1/user":
                self.assertEqual(request.headers["Authorization"], "Bearer user-token")
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            self.sent.append((request.url.path, json.loads(request.content), request.headers.get("Authorization")))
            return data(request)
        return TestClient(create_app(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    def test_owner_routes_require_identity_before_any_data_request(self):
        with self.client(lambda _: self.fail("Unauthenticated data request")) as client:
            for method in ("POST", "GET", "DELETE"):
                self.assertEqual(client.request(method, f"/v1/projects/{PROJECT}/share").status_code, 401)

    def test_create_returns_the_token_once_and_stores_only_its_hash(self):
        with self.client(lambda _: httpx.Response(200, json={"createdAt": DATE, "expiresAt": LATER})) as client:
            first = client.post(f"/v1/projects/{PROJECT}/share", headers=TOKEN)
            second = client.post(f"/v1/projects/{PROJECT}/share", headers=TOKEN)
        self.assertEqual(first.status_code, 201)
        token = first.json()["token"]
        self.assertRegex(token, f"^{project_config.PROJECT_SHARE_TOKEN_PATTERN}$")
        self.assertNotEqual(token, second.json()["token"])
        path, body, authorization = self.sent[0]
        self.assertEqual(path, "/rest/v1/rpc/pf_share_project")
        self.assertEqual(body, {"p_id": PROJECT, "p_token_sha256": hashlib.sha256(token.encode()).hexdigest()})
        self.assertNotIn(token, json.dumps(body))
        self.assertEqual(authorization, "Bearer user-token")
        self.assertEqual(first.json()["share"]["expiresAt"], "2026-11-17T08:00:00.123456Z")

    def test_state_and_revocation_never_carry_the_token(self):
        answers = iter([httpx.Response(200, json={"createdAt": DATE, "expiresAt": LATER}), httpx.Response(200, content=b"null"), httpx.Response(204)])
        with self.client(lambda _: next(answers)) as client:
            active = client.get(f"/v1/projects/{PROJECT}/share", headers=TOKEN)
            none = client.get(f"/v1/projects/{PROJECT}/share", headers=TOKEN)
            removed = client.delete(f"/v1/projects/{PROJECT}/share", headers=TOKEN)
        self.assertEqual(active.json(), {"share": {"createdAt": "2026-11-10T08:00:00.123456Z", "expiresAt": "2026-11-17T08:00:00.123456Z"}})
        self.assertNotIn("token", active.text)
        self.assertEqual(none.json(), {"share": None})
        self.assertEqual(removed.status_code, 204)
        self.assertEqual([path for path, _, _ in self.sent], ["/rest/v1/rpc/pf_project_share", "/rest/v1/rpc/pf_project_share", "/rest/v1/rpc/pf_unshare_project"])

    def test_a_visitor_reads_with_the_public_key_only_and_by_hash(self):
        with self.client(lambda _: httpx.Response(200, json=SHARED)) as client:
            response = client.post("/v1/shared", json={"token": LINK})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["activeSequence"], "ACDE")
        path, body, authorization = self.sent[0]
        self.assertEqual((path, body), ("/rest/v1/rpc/pf_shared_project", {"p_token_sha256": hashlib.sha256(LINK.encode()).hexdigest()}))
        self.assertIsNone(authorization)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_wrong_revoked_and_malformed_links_look_the_same_and_skip_the_database_when_malformed(self):
        with self.client(lambda _: httpx.Response(200, content=b"null")) as client:
            unknown = client.post("/v1/shared", json={"token": LINK})
            malformed = client.post("/v1/shared", json={"token": "short"})
            oversized = client.post("/v1/shared", json={"token": "A" * 200})
        self.assertEqual((unknown.status_code, malformed.status_code), (404, 404))
        self.assertEqual(unknown.json()["detail"] if "detail" in unknown.json() else unknown.text, malformed.json()["detail"] if "detail" in malformed.json() else malformed.text)
        self.assertEqual(oversized.status_code, 422)
        self.assertEqual(len(self.sent), 1)

    def test_a_stored_snapshot_with_the_laboratory_is_refused(self):
        laboratory = {**SHARED, "workspace": {**WORKSPACE, "schemaVersion": 2}}
        with self.client(lambda _: httpx.Response(200, json=laboratory)) as client:
            self.assertEqual(client.post("/v1/shared", json={"token": LINK}).status_code, 502)

    def test_reads_are_budgeted_per_visitor_address(self):
        with self.client(lambda _: httpx.Response(200, json=SHARED), replace(SETTINGS, shared_requests_per_minute=2)) as client:
            codes = [client.post("/v1/shared", json={"token": LINK}, headers={"X-Forwarded-For": "203.0.113.5"}).status_code for _ in range(3)]
            other = client.post("/v1/shared", json={"token": LINK}, headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"})
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(other.status_code, 200)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(load_settings({}).shared_requests_per_minute, 30)
        self.assertEqual(load_settings({"SHARED_REQUESTS_PER_MINUTE": "0"}).shared_requests_per_minute, 0)

    def test_rpc_creates_reads_and_opens_without_a_bearer_for_the_visitor(self):
        def data(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("pf_shared_project"):
                return httpx.Response(200, json=SHARED)
            if request.url.path.endswith("pf_unshare_project"):
                return httpx.Response(204)
            return httpx.Response(200, json={"createdAt": DATE, "expiresAt": LATER})
        with self.client(data) as client:
            created = client.post(f"{PROJECTS}/CreateProjectShare", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT}))
            state = client.post(f"{PROJECTS}/GetProjectShare", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT}))
            removed = client.post(f"{PROJECTS}/DeleteProjectShare", headers={**CONNECT, **TOKEN}, content=json.dumps({"projectId": PROJECT}))
            opened = client.post(f"{PROJECTS}/GetSharedProject", headers=CONNECT, content=json.dumps({"token": LINK}))
            unsigned = client.post(f"{PROJECTS}/CreateProjectShare", headers=CONNECT, content=json.dumps({"projectId": PROJECT}))
        self.assertRegex(created.json()["token"], f"^{project_config.PROJECT_SHARE_TOKEN_PATTERN}$")
        self.assertIn("expireTime", state.json()["share"])
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(opened.json()["project"]["activeSequence"], "ACDE")
        self.assertEqual(opened.json()["project"]["workspace"]["schemaVersion"], 1)
        self.assertEqual(unsigned.json()["code"], "unauthenticated")
        self.assertIsNone(self.sent[-1][2])

    def test_migration_keeps_the_lifetime_of_the_code_and_lets_visitors_call_one_function(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        days = re.search(r"interval '(\d+) days'\) -- PROJECT_SHARE_LIFETIME_DAYS", sql)
        self.assertIsNotNone(days)
        self.assertEqual(int(days.group(1)), project_config.PROJECT_SHARE_LIFETIME_DAYS)
        self.assertEqual(re.findall(r"grant execute on function (public\.\w+)\([^)]*\) to anon", sql), ["public.pf_shared_project"])
        self.assertIn("grant select on public.project_shares to authenticated;", sql)
        self.assertNotRegex(sql, r"grant\s+(insert|update|delete)[^;]*project_shares")


if __name__ == "__main__":
    unittest.main()
