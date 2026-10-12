"""HTTP contracts and forwarding authorization, with a simulated Supabase transport."""
import copy
import unittest
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

USER = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000010"
DATE = "2026-10-12T08:00:00.123456+00:00"
WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "descriptors": None, "alignment": {"first": "ACD", "second": "ACD", "matrix": "BLOSUM62", "mode": "global", "result": None}}
WRITE = {"name": "Insulin study", "activeSequence": "ACD", "workspace": WORKSPACE}
DOCUMENT = {**WRITE, "id": PROJECT, "status": "draft", "createdAt": DATE, "updatedAt": DATE}
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key")
HEADERS = {"Authorization": "Bearer user-token"}


class ProjectTests(unittest.TestCase):
    def client(self, data_handler):
        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer user-token")
            self.assertEqual(request.headers["apikey"], "public-key")
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            return data_handler(request)
        return TestClient(create_app(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    def test_all_project_routes_require_identity_before_touching_data(self):
        with self.client(lambda _: self.fail("Unauthenticated data request")) as client:
            for method, path, payload in [("GET", "/v1/projects", None), ("POST", "/v1/projects", WRITE), ("GET", f"/v1/projects/{PROJECT}", None), ("PUT", f"/v1/projects/{PROJECT}", {**WRITE, "expectedUpdatedAt": DATE}), ("DELETE", f"/v1/projects/{PROJECT}?expectedUpdatedAt={DATE.replace('+', '%2B')}", None)]:
                self.assertEqual(client.request(method, path, json=payload).status_code, 401)

    def test_create_forwards_user_token_and_does_not_accept_owner(self):
        import json
        def data(request):
            self.assertEqual(request.url.path, "/rest/v1/rpc/pf_save_project")
            body = json.loads(request.content)
            self.assertIsNone(body["p_id"])
            self.assertNotIn("owner_id", body)
            self.assertEqual(body["p_workspace"], WORKSPACE)
            return httpx.Response(200, json=DOCUMENT)
        with self.client(data) as client:
            response = client.post("/v1/projects", headers=HEADERS, json=WRITE)
            forged = client.post("/v1/projects", headers=HEADERS, json={**WRITE, "ownerId": "other"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["updatedAt"], DATE)
        self.assertEqual(forged.status_code, 422)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_listing_has_owner_filter_and_bounded_pagination(self):
        def data(request):
            query = parse_qs(request.url.query.decode())
            self.assertEqual(query["owner_id"], [f"eq.{USER}"])
            self.assertEqual(query["limit"], ["2"])
            self.assertNotIn("active_sequence", query["select"][0])
            row = {"id": PROJECT, "name": "Test", "status": "draft", "created_at": DATE, "updated_at": DATE}
            return httpx.Response(200, json=[row, row])
        with self.client(data) as client:
            response = client.get("/v1/projects?limit=1", headers=HEADERS)
            invalid = client.get("/v1/projects?limit=10000", headers=HEADERS)
        self.assertEqual(response.json()["nextOffset"], 1)
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(invalid.status_code, 422)

    def test_update_delete_conflicts_and_hidden_foreign_resources(self):
        for code, status in [("PT409", 409), ("PT404", 404), ("PGRST202", 503), ("PGRST205", 503)]:
            with self.subTest(code=code), self.client(lambda _, c=code: httpx.Response(400, json={"code": c, "message": "private sequence or SQL"})) as client:
                update = client.put(f"/v1/projects/{PROJECT}", headers=HEADERS, json={**WRITE, "expectedUpdatedAt": DATE})
                delete = client.delete(f"/v1/projects/{PROJECT}", headers=HEADERS, params={"expectedUpdatedAt": DATE})
                self.assertEqual(update.status_code, status)
                self.assertEqual(delete.status_code, status)
                self.assertNotIn("private", update.text)
        with self.client(lambda _: httpx.Response(200, content=b"null")) as client:
            self.assertEqual(client.get(f"/v1/projects/{PROJECT}", headers=HEADERS).status_code, 404)

    def test_valid_delete_and_update_preserve_microsecond_version(self):
        import json
        def data(request):
            self.assertEqual(json.loads(request.content)["p_expected_updated_at"], DATE)
            return httpx.Response(200, json={} if "delete" in request.url.path else DOCUMENT)
        with self.client(data) as client:
            self.assertEqual(client.put(f"/v1/projects/{PROJECT}", headers=HEADERS, json={**WRITE, "expectedUpdatedAt": DATE}).status_code, 200)
            response = client.delete(f"/v1/projects/{PROJECT}", headers=HEADERS, params={"expectedUpdatedAt": DATE})
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b"")

    def test_rejects_stale_descriptors_invalid_sequences_and_unversioned_update(self):
        invalid = [
            {**WRITE, "activeSequence": "AC!"}, {**WRITE, "name": " "}, {**WRITE, "activeSequence": "A" * 100001},
            {**WRITE, "workspace": {**WORKSPACE, "descriptors": {"sequence": "AAA", "excluded": 0, "methodVersion": "proteinforge-descriptors-v1", "result": {"massDa": 100, "isoelectricPoint": 7, "instabilityIndex": 10, "aliphaticIndex": 10, "gravy": 1, "length": 3}}}},
        ]
        with self.client(lambda _: self.fail("Invalid input reached data")) as client:
            for body in invalid:
                response = client.post("/v1/projects", headers=HEADERS, json=body)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("AAA", response.text)
            self.assertEqual(client.put(f"/v1/projects/{PROJECT}", headers=HEADERS, json=WRITE).status_code, 422)

    def test_rejects_alignment_that_does_not_match_its_inputs(self):
        body = copy.deepcopy(WRITE)
        body["workspace"]["alignment"]["result"] = {"alignedFirst": "AAA", "alignedSecond": "AAA", "marks": "|||", "score": 10, "identities": 3, "similarities": 3, "columns": 3, "parameters": {"matrix": "BLOSUM62", "mode": "global", "gapOpen": 10, "gapExtend": 0.5, "terminalGaps": "free"}}
        with self.client(lambda _: self.fail("Stale result reached data")) as client:
            self.assertEqual(client.post("/v1/projects", headers=HEADERS, json=body).status_code, 422)

    def test_provider_failures_are_safe(self):
        for handler, expected in [(lambda _: httpx.Response(200, json={"wrong": "value"}), 502), (lambda _: httpx.Response(500, text="secret"), 502)]:
            with self.client(handler) as client:
                response = client.get(f"/v1/projects/{PROJECT}", headers=HEADERS)
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("secret", response.text)
