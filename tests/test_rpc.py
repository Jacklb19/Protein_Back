"""RPC contracts over Connect (JSON) and gRPC-Web (binary), with simulated Supabase and UniProt transports."""
import json
import struct
import unittest

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rpc import convert
from app.rpc.gen.proteinforge.account.v1.account_service_pb import GetStatusRequest, GetStatusResponse

USER = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000010"
DATE = "2026-10-12T08:00:00.123456+00:00"
WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "source": None, "descriptors": None, "alignment": {"first": "ACD", "second": "ACD", "matrix": "BLOSUM62", "mode": "global", "result": None}}
DOCUMENT = {"name": "Insulin study", "activeSequence": "ACD", "workspace": WORKSPACE, "id": PROJECT, "status": "draft", "createdAt": DATE, "updatedAt": DATE}
RPC_WORKSPACE = {"schemaVersion": 1, "profileWindow": 9, "alignment": {"first": "ACD", "second": "ACD", "matrix": "SUBSTITUTION_MATRIX_BLOSUM62", "mode": "ALIGNMENT_MODE_GLOBAL"}}
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", allowed_origins=("http://127.0.0.1:5173",))
TOKEN = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
ACCOUNT = "/proteinforge.account.v1.AccountService"
PROJECTS = "/proteinforge.project.v1.ProjectService"
REFERENCE = "/proteinforge.reference.v1.ReferenceService"


def grpc_web_frames(body: bytes) -> list[tuple[int, bytes]]:
    frames, position = [], 0
    while position < len(body):
        flags, length = struct.unpack(">BI", body[position:position + 5])
        frames.append((flags, body[position + 5:position + 5 + length]))
        position += 5 + length
    return frames


class RpcTests(unittest.TestCase):
    def client(self, data_handler=None):
        def handler(request):
            if request.url.path == "/auth/v1/user":
                self.assertEqual(request.headers["Authorization"], "Bearer user-token")
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            if data_handler is None:
                self.fail(f"Unexpected request to {request.url}")
            return data_handler(request)
        return TestClient(create_app(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    def call(self, client, path, message, headers=None):
        return client.post(path, headers={**CONNECT, **(headers or {})}, content=json.dumps(message))

    def test_status_over_connect_and_grpc_web_with_correlation(self):
        with self.client() as client:
            connect = self.call(client, f"{ACCOUNT}/GetStatus", {}, {"X-Request-Id": "trace-1"})
            frame = struct.pack(">BI", 0, 0) + GetStatusRequest().to_binary()
            web = client.post(f"{ACCOUNT}/GetStatus", headers={"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1"}, content=frame)
        self.assertEqual(connect.status_code, 200)
        self.assertEqual(connect.json(), {"status": "ok", "version": "0.1.0", "identityConfigured": True})
        self.assertEqual(connect.headers["X-Request-Id"], "trace-1")
        self.assertEqual(connect.headers["Cache-Control"], "no-store")
        frames = grpc_web_frames(web.content)
        self.assertEqual(GetStatusResponse.from_binary(frames[0][1]).version, "0.1.0")
        self.assertEqual(frames[-1][0] & 0x80, 0x80)
        self.assertIn(b"grpc-status:0", frames[-1][1].replace(b" ", b""))

    def test_protected_calls_require_identity_before_touching_data(self):
        with self.client() as client:
            for path, message in [(f"{ACCOUNT}/GetMe", {}), (f"{PROJECTS}/ListProjects", {}), (f"{PROJECTS}/GetProject", {"id": PROJECT}),
                                  (f"{PROJECTS}/CreateProject", {"name": "x", "activeSequence": "ACD", "workspace": RPC_WORKSPACE}),
                                  (f"{PROJECTS}/DeleteProject", {"id": PROJECT, "expectedUpdateTime": DATE}), (f"{REFERENCE}/SearchEntries", {"query": "insulin"})]:
                with self.subTest(path=path):
                    response = self.call(client, path, message)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), {"code": "unauthenticated", "message": "missing-token"})

    def test_me_returns_the_verified_identity(self):
        with self.client() as client:
            response = self.call(client, f"{ACCOUNT}/GetMe", {}, TOKEN)
        self.assertEqual(response.json(), {"id": USER, "email": "a@example.org"})

    def test_listing_uses_owner_filter_and_opaque_page_tokens(self):
        def data(request):
            self.assertEqual(request.url.path, "/rest/v1/rpc/pf_list_projects")
            self.assertNotIn("owner_id", json.loads(request.content))
            row = {"id": PROJECT, "name": "Test", "status": "draft", "created_at": DATE, "updated_at": DATE}
            return httpx.Response(200, json=[row, row])
        with self.client(data) as client:
            first = self.call(client, f"{PROJECTS}/ListProjects", {"pageSize": 1}, TOKEN).json()
            second = self.call(client, f"{PROJECTS}/ListProjects", {"pageSize": 1, "pageToken": first["nextPageToken"]}, TOKEN)
            forged = self.call(client, f"{PROJECTS}/ListProjects", {"pageToken": "not-a-token"}, TOKEN)
        self.assertEqual(len(first["projects"]), 1)
        self.assertEqual(first["projects"][0]["status"], "PROJECT_STATUS_DRAFT")
        self.assertEqual(convert.page_offset(first["nextPageToken"], 10), 1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(forged.json(), {"code": "invalid_argument", "message": "validation: page_token"})

    def test_preview_and_global_query_preserve_saved_evidence_and_bound_pagination(self):
        preview = {"sequenceLength": 3, "sequencePrefix": "ACD", "excluded": 0, "descriptors": None, "source": {"origin": "catalog", "accession": "P01308"}, "alignment": {"firstLength": 3, "secondLength": 3, "firstPrefix": "ACD", "secondPrefix": "ACD", "matrix": "BLOSUM62", "mode": "global", "result": None}}
        def data(request):
            body = json.loads(request.content)
            self.assertEqual(body["p_query"], "Insulin")
            self.assertEqual(body["p_order"], "name")
            self.assertEqual(body["p_prefix_length"], 40)
            row = {"id": PROJECT, "name": "Insulin", "status": "draft", "created_at": DATE, "updated_at": DATE, "preview": preview}
            return httpx.Response(200, json=[row, row])
        with self.client(data) as client:
            first = self.call(client, f"{PROJECTS}/ListProjects", {"pageSize": 1, "query": " Insulin ", "order": "PROJECT_ORDER_NAME"}, TOKEN)
            self.assertEqual(first.status_code, 200, first.text)
            document = first.json()
            self.assertTrue(document["queryApplied"])
            self.assertEqual(document["projects"][0]["preview"]["sequencePrefix"], "ACD")
            self.assertNotIn("descriptors", document["projects"][0]["preview"])
            token = document["nextPageToken"]
            second = self.call(client, f"{PROJECTS}/ListProjects", {"pageSize": 1, "query": "Insulin", "order": "PROJECT_ORDER_NAME", "pageToken": token}, TOKEN)
            changed = self.call(client, f"{PROJECTS}/ListProjects", {"query": "Other", "pageToken": token}, TOKEN)
            invalid = self.call(client, f"{PROJECTS}/ListProjects", {"query": "x" * 121}, TOKEN)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(changed.json()["code"], "invalid_argument")
        self.assertEqual(invalid.json(), {"code": "invalid_argument", "message": "validation: query"})

    def test_invalid_preview_does_not_leak_stored_fields(self):
        def data(_request):
            return httpx.Response(200, json=[{"id": PROJECT, "name": "private-name", "status": "draft", "created_at": DATE, "updated_at": DATE, "preview": {"sequenceLength": 999, "sequencePrefix": "private-sequence", "excluded": 0}}])
        with self.client(data) as client:
            response = self.call(client, f"{PROJECTS}/ListProjects", {}, TOKEN)
        self.assertEqual(response.json(), {"code": "unavailable", "message": "projects-invalid-response"})

    def test_create_maps_messages_to_the_rest_contract(self):
        def data(request):
            self.assertEqual(request.url.path, "/rest/v1/rpc/pf_save_project")
            body = json.loads(request.content)
            self.assertIsNone(body["p_id"])
            self.assertEqual(body["p_workspace"], WORKSPACE)
            return httpx.Response(200, json=DOCUMENT)
        with self.client(data) as client:
            response = self.call(client, f"{PROJECTS}/CreateProject", {"name": "Insulin study", "activeSequence": "ACD", "workspace": RPC_WORKSPACE}, TOKEN)
        project = response.json()["project"]
        self.assertEqual(project["summary"]["id"], PROJECT)
        self.assertEqual(project["workspace"]["alignment"]["matrix"], "SUBSTITUTION_MATRIX_BLOSUM62")
        self.assertTrue(project["summary"]["updateTime"].startswith("2026-10-12T08:00:00.123456"))

    def test_unset_optional_versions_stay_empty(self):
        # A bundled example has no entry version; it must reach the contract as null, not 0.
        source = {"origin": "SOURCE_ORIGIN_CATALOG", "accession": "P01308", "sequence": "ACD", "sequenceVersion": 1}
        def data(request):
            body = json.loads(request.content)
            self.assertEqual(body["p_workspace"]["source"], {"origin": "catalog", "accession": "P01308", "sequence": "ACD", "entryVersion": None, "sequenceVersion": 1, "retrievedAt": None})
            return httpx.Response(200, json={**DOCUMENT, "workspace": body["p_workspace"]})
        with self.client(data) as client:
            response = self.call(client, f"{PROJECTS}/CreateProject", {"name": "Insulin study", "activeSequence": "ACD", "workspace": {**RPC_WORKSPACE, "source": source}}, TOKEN)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project"]["workspace"]["source"]["sequenceVersion"], 1)
        self.assertNotIn("entryVersion", response.json()["project"]["workspace"]["source"])

    def test_validation_names_fields_without_echoing_values(self):
        with self.client(lambda _: self.fail("Invalid data reached Supabase")) as client:
            response = self.call(client, f"{PROJECTS}/CreateProject", {"name": "x", "activeSequence": "ACD<script>", "workspace": RPC_WORKSPACE}, TOKEN)
            unknown = self.call(client, f"{PROJECTS}/GetProject", {"id": "not-a-uuid"}, TOKEN)
            missing = self.call(client, f"{PROJECTS}/DeleteProject", {"id": PROJECT}, TOKEN)
        self.assertEqual(response.json()["code"], "invalid_argument")
        self.assertTrue(response.json()["message"].startswith("validation: "))
        self.assertNotIn("script", response.text)
        self.assertEqual(unknown.json()["message"], "validation: id")
        self.assertEqual(missing.json()["message"], "validation: expected_update_time")

    def test_storage_failures_map_to_rpc_codes(self):
        update = {"id": PROJECT, "expectedUpdateTime": DATE, "name": "x", "activeSequence": "ACD", "workspace": RPC_WORKSPACE}
        for supabase_code, expected in [("PT409", "aborted"), ("PT404", "not_found"), ("PGRST202", "failed_precondition")]:
            with self.subTest(code=supabase_code), self.client(lambda _, c=supabase_code: httpx.Response(400, json={"code": c, "message": "private SQL"})) as client:
                response = self.call(client, f"{PROJECTS}/UpdateProject", update, TOKEN)
                self.assertEqual(response.json()["code"], expected)
                self.assertNotIn("private", response.text)

    def test_reference_entries_are_validated(self):
        with self.client(lambda _: self.fail("Invalid accession reached UniProt")) as client:
            entry = self.call(client, f"{REFERENCE}/GetEntry", {"accession": "not-an-accession"}, TOKEN)
            query = self.call(client, f"{REFERENCE}/SearchEntries", {"query": "x"}, TOKEN)
        self.assertEqual(entry.json()["code"], "invalid_argument")
        self.assertEqual(query.json()["code"], "invalid_argument")

    def test_reference_budget_is_shared_with_rest_and_reported_as_resource_exhausted(self):
        budgeted = Settings(supabase_url=SETTINGS.supabase_url, supabase_anon_key=SETTINGS.supabase_anon_key,
                            allowed_origins=SETTINGS.allowed_origins, uniprot_requests_per_minute=1)

        def handler(request):
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            return httpx.Response(200, json={"results": []})

        with TestClient(create_app(budgeted, httpx.AsyncClient(transport=httpx.MockTransport(handler)))) as client:
            first = self.call(client, f"{REFERENCE}/SearchEntries", {"query": "insulin"}, TOKEN)
            refused = self.call(client, f"{REFERENCE}/SearchEntries", {"query": "insulin"}, TOKEN)
            rest = client.get("/v1/uniprot/search?query=insulin", headers=TOKEN)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(refused.json(), {"code": "resource_exhausted", "message": "uniprot-rate-limit"})
        self.assertEqual(rest.status_code, 429)

    def test_browser_preflight_allows_rpc_headers_and_rest_keeps_working(self):
        with self.client() as client:
            preflight = client.options(f"{ACCOUNT}/GetStatus", headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-grpc-web,x-user-agent,connect-timeout-ms"})
            health = client.get("/v1/health")
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
