"""Report storage contracts over REST and Connect, with simulated Supabase Auth, PostgREST and Storage."""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import report_config
from app.config import Settings
from app.main import create_app

USER = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000010"
REPORT = "00000000-0000-7000-8000-000000000020"
DATE = "2026-11-01T08:00:00.123456Z"
SHA = "a" * 64
PATH = f"{USER}/{REPORT}.pdf"
RECORD = {"id": REPORT, "projectId": PROJECT, "kind": "pdf", "sizeBytes": 1200, "sha256": SHA, "status": "pending", "createdAt": DATE, "completedAt": None}
READY = {**RECORD, "status": "ready", "completedAt": DATE}
UPLOAD = {"projectId": PROJECT, "kind": "pdf", "sizeBytes": 1200, "sha256": SHA}
SETTINGS = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key")
HEADERS = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1", **HEADERS}
SERVICE = "/proteinforge.report.v1.ReportService"
MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261101000000_report_storage.sql"


class ReportTests(unittest.TestCase):
    def client(self, routes):
        """`routes` maps 'METHOD path' to a handler; any other data or storage call fails the test."""
        self.calls = []

        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer user-token")
            self.assertEqual(request.headers["apikey"], "public-key")
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            key = f"{request.method} {request.url.path}"
            self.calls.append((key, json.loads(request.content) if request.content else None))
            if key not in routes:
                self.fail(f"Unexpected request {key}")
            return routes[key](request)
        return TestClient(create_app(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    @staticmethod
    def answer(body, status=200):
        return lambda _request: httpx.Response(status, content=json.dumps(body).encode())

    def reserve_routes(self, sign=None):
        return {
            "POST /rest/v1/rpc/pf_reserve_report": self.answer({"report": RECORD, "objectPath": PATH}),
            f"POST /storage/v1/object/upload/sign/reports/{PATH}": sign or self.answer({"url": f"/object/upload/sign/reports/{PATH}?token=signed"}),
            "POST /rest/v1/rpc/pf_delete_report": self.answer({}),
        }

    def test_migration_repeats_the_configured_limits(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        kinds = "(" + ", ".join(f"'{kind}'" for kind in report_config.REPORT_KINDS) + ")"
        mimes = ", ".join(f"'{mime}'" for mime in dict.fromkeys(value[0] for value in report_config.REPORT_KINDS.values()))
        self.assertIn(f"('{report_config.REPORT_BUCKET}', '{report_config.REPORT_BUCKET}', false, {report_config.MAX_REPORT_BYTES}, array[{mimes}])", sql)
        self.assertEqual(sql.count(kinds), 2)
        self.assertEqual(sql.count(f"between 1 and {report_config.MAX_REPORT_BYTES}"), 2)
        self.assertIn(f"stored_count >= {report_config.MAX_REPORTS_PER_OWNER} or stored_bytes + new.size_bytes > {report_config.MAX_OWNER_REPORT_BYTES}", sql)
        self.assertEqual(sql.count(f"interval '{report_config.UPLOAD_URL_SECONDS} seconds'"), 2)
        self.assertIn(f"p_limit not between 1 and {report_config.MAX_REPORT_PAGE_SIZE + 1} or p_offset is null or p_offset not between 0 and {report_config.MAX_REPORT_OFFSET}", sql)
        # The SQL CASE expressions name each non-CSV kind and send every CSV kind to `else`.
        explicit = {kind: value for kind, value in report_config.REPORT_KINDS.items() if value[1] != "csv"}
        self.assertEqual({value[0] for value in report_config.REPORT_KINDS.values() if value[1] == "csv"}, {"text/csv"})
        self.assertIn(" ".join(f"when '{kind}' then '.{extension}'" for kind, (_mime, extension, _name) in explicit.items()) + " else '.csv' end", sql)
        self.assertIn(" ".join(f"when '{kind}' then '{mime}'" for kind, (mime, _extension, _name) in explicit.items()) + " else 'text/csv' end", sql)
        for function in report_config.REPORT_RPC.values():
            self.assertIn(f"public.{function.removeprefix('rpc/')}(", sql)

    def test_every_route_requires_identity_before_touching_data(self):
        with self.client({}) as client:
            for method, path, body in [("POST", "/v1/reports", UPLOAD), ("GET", "/v1/reports", None), ("POST", f"/v1/reports/{REPORT}/complete", None),
                                       ("POST", f"/v1/reports/{REPORT}/download", None), ("DELETE", f"/v1/reports/{REPORT}", None)]:
                self.assertEqual(client.request(method, path, json=body).status_code, 401)
            for method in ["CreateReportUpload", "CompleteReport", "ListReports", "CreateReportDownload", "DeleteReport"]:
                response = client.post(f"{SERVICE}/{method}", headers={"Content-Type": "application/json", "Connect-Protocol-Version": "1"}, content="{}")
                self.assertEqual(response.json()["code"], "unauthenticated")
        self.assertEqual(self.calls, [])

    def test_upload_reserves_then_signs_with_the_user_token_and_never_takes_an_owner(self):
        with self.client(self.reserve_routes()) as client:
            response = client.post("/v1/reports", headers=HEADERS, json=UPLOAD)
            forged = client.post("/v1/reports", headers=HEADERS, json={**UPLOAD, "ownerId": USER})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        body = response.json()
        self.assertEqual(body["uploadUrl"], f"https://project.supabase.co/storage/v1/object/upload/sign/reports/{PATH}?token=signed")
        self.assertEqual(body["contentType"], "application/pdf")
        self.assertEqual(datetime.fromisoformat(body["expiresAt"]) - datetime.fromisoformat(DATE), timedelta(seconds=report_config.UPLOAD_URL_SECONDS))
        self.assertEqual(body["report"], RECORD)
        self.assertNotIn("objectPath", json.dumps(body))
        self.assertEqual(self.calls[0], ("POST /rest/v1/rpc/pf_reserve_report", {"p_project_id": PROJECT, "p_kind": "pdf", "p_size_bytes": 1200, "p_sha256": SHA}))
        self.assertEqual(self.calls[1], (f"POST /storage/v1/object/upload/sign/reports/{PATH}", {}))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(forged.status_code, 422)

    def test_invalid_kind_size_hash_or_project_never_reach_data(self):
        invalid = [{**UPLOAD, "kind": "docx"}, {**UPLOAD, "sizeBytes": 0}, {**UPLOAD, "sizeBytes": report_config.MAX_REPORT_BYTES + 1},
                   {**UPLOAD, "sha256": "A" * 64}, {**UPLOAD, "sha256": SHA[:-1]}, {**UPLOAD, "projectId": "not-a-uuid"}]
        with self.client({}) as client:
            for body in invalid:
                with self.subTest(body=body):
                    self.assertEqual(client.post("/v1/reports", headers=HEADERS, json=body).status_code, 422)
            self.assertEqual(client.get("/v1/reports?limit=51", headers=HEADERS).status_code, 422)
            self.assertEqual(client.post("/v1/reports/not-a-uuid/complete", headers=HEADERS).status_code, 422)
        self.assertEqual(self.calls, [])

    def test_failed_signature_releases_the_reservation(self):
        cases = [(self.answer({"statusCode": "404", "error": "not_found", "message": f"Bucket not found {PATH}"}, 400), 503, "reports-storage-unavailable"),
                 (self.answer({"statusCode": "403", "message": "new row violates row-level security policy"}, 400), 403, "report-forbidden"),
                 (self.answer({"error": "boom"}, 500), 502, "reports-storage-error"),
                 (self.answer({"url": "https://elsewhere.example/upload"}), 502, "reports-invalid-response")]
        for sign, status, code in cases:
            with self.subTest(code=code), self.client(self.reserve_routes(sign)) as client:
                response = client.post("/v1/reports", headers=HEADERS, json=UPLOAD)
                self.assertEqual(response.status_code, status)
                self.assertTrue(response.json()["type"].endswith(code))
                self.assertNotIn(USER, response.text)
                self.assertEqual(self.calls[-1], ("POST /rest/v1/rpc/pf_delete_report", {"p_id": REPORT}))

    def test_object_outside_the_caller_folder_is_rejected(self):
        routes = self.reserve_routes()
        routes["POST /rest/v1/rpc/pf_reserve_report"] = self.answer({"report": RECORD, "objectPath": f"00000000-0000-4000-8000-000000000002/{REPORT}.pdf"})
        with self.client(routes) as client:
            self.assertEqual(client.post("/v1/reports", headers=HEADERS, json=UPLOAD).status_code, 502)
        self.assertEqual(len(self.calls), 1)

    def test_sql_messages_become_stable_codes_without_provider_text(self):
        cases = [("POST /rest/v1/rpc/pf_reserve_report", "PT413", "Report quota exceeded", 413, "report-quota-exceeded"),
                 ("POST /rest/v1/rpc/pf_reserve_report", "PT404", "Project not found", 404, "project-not-found"),
                 ("POST /rest/v1/rpc/pf_reserve_report", "PT422", "Invalid report", 422, "validation"),
                 ("POST /rest/v1/rpc/pf_reserve_report", "PGRST202", "Could not find the function", 503, "reports-schema-unavailable"),
                 ("POST /rest/v1/rpc/pf_complete_report", "PT409", "Report upload missing", 409, "report-upload-missing"),
                 ("POST /rest/v1/rpc/pf_complete_report", "PT404", "Report not found", 404, "report-not-found")]
        for route, code, message, status, detail in cases:
            with self.subTest(detail=detail), self.client({route: self.answer({"code": code, "message": message, "details": SHA}, 400)}) as client:
                path = "/v1/reports" if "reserve" in route else f"/v1/reports/{REPORT}/complete"
                response = client.post(path, headers=HEADERS, json=UPLOAD if "reserve" in route else None)
                self.assertEqual(response.status_code, status)
                self.assertTrue(response.json()["type"].endswith(detail))
                self.assertNotIn(SHA, response.text)

    def test_complete_and_list_are_validated_and_paginated(self):
        rows = [{"id": REPORT, "project_id": None, "kind": "json", "size_bytes": 10, "sha256": SHA, "status": "ready", "created_at": DATE, "completed_at": DATE}] * 3
        routes = {"POST /rest/v1/rpc/pf_complete_report": self.answer(READY), "POST /rest/v1/rpc/pf_list_reports": self.answer(rows)}
        with self.client(routes) as client:
            completed = client.post(f"/v1/reports/{REPORT}/complete", headers=HEADERS)
            listing = client.get(f"/v1/reports?limit=2&projectId={PROJECT}", headers=HEADERS)
        self.assertEqual(completed.json(), READY)
        self.assertEqual(listing.json()["nextOffset"], 2)
        self.assertEqual(len(listing.json()["items"]), 2)
        self.assertIsNone(listing.json()["items"][0]["projectId"])
        self.assertEqual(self.calls[1][1], {"p_limit": 3, "p_offset": 0, "p_project_id": PROJECT})
        with self.client({"POST /rest/v1/rpc/pf_list_reports": self.answer([{**rows[0], "kind": "docx"}])}) as client:
            self.assertEqual(client.get("/v1/reports", headers=HEADERS).status_code, 502)

    def test_download_signs_only_ready_reports_for_a_short_time(self):
        signed = {"signedURL": f"/object/sign/reports/{PATH}?token=short"}
        routes = {"POST /rest/v1/rpc/pf_report_object": self.answer({"report": READY, "objectPath": PATH}),
                  f"POST /storage/v1/object/sign/reports/{PATH}": self.answer(signed)}
        with self.client(routes) as client:
            response = client.post(f"/v1/reports/{REPORT}/download", headers=HEADERS)
        body = response.json()
        self.assertEqual(body["downloadUrl"], f"https://project.supabase.co/storage/v1/object/sign/reports/{PATH}?token=short&download=proteinforge-report.pdf")
        self.assertEqual(body["fileName"], "proteinforge-report.pdf")
        self.assertEqual(self.calls[1][1], {"expiresIn": report_config.DOWNLOAD_URL_SECONDS})
        self.assertLessEqual(datetime.fromisoformat(body["expiresAt"]) - datetime.now(datetime.fromisoformat(DATE).tzinfo), timedelta(seconds=report_config.DOWNLOAD_URL_SECONDS))
        for stored, status in [({"report": RECORD, "objectPath": PATH}, 409), (None, 404)]:
            with self.subTest(status=status), self.client({"POST /rest/v1/rpc/pf_report_object": self.answer(stored)}) as client:
                self.assertEqual(client.post(f"/v1/reports/{REPORT}/download", headers=HEADERS).status_code, status)
                self.assertEqual(len(self.calls), 1)
        routes[f"POST /storage/v1/object/sign/reports/{PATH}"] = self.answer({"statusCode": "404", "message": "Object not found"}, 400)
        with self.client(routes) as client:
            response = client.post(f"/v1/reports/{REPORT}/download", headers=HEADERS)
        self.assertTrue(response.json()["type"].endswith("report-object-missing"))

    def test_delete_removes_the_object_before_the_record(self):
        routes = {"POST /rest/v1/rpc/pf_report_object": self.answer({"report": READY, "objectPath": PATH}),
                  "DELETE /storage/v1/object/reports": self.answer([]),
                  "POST /rest/v1/rpc/pf_delete_report": self.answer({})}
        with self.client(routes) as client:
            response = client.delete(f"/v1/reports/{REPORT}", headers=HEADERS)
        self.assertEqual(response.status_code, 204)
        self.assertEqual([call[0] for call in self.calls], ["POST /rest/v1/rpc/pf_report_object", "DELETE /storage/v1/object/reports", "POST /rest/v1/rpc/pf_delete_report"])
        self.assertEqual(self.calls[1][1], {"prefixes": [PATH]})
        routes["DELETE /storage/v1/object/reports"] = self.answer({"error": "down"}, 500)
        with self.client(routes) as client:
            self.assertEqual(client.delete(f"/v1/reports/{REPORT}", headers=HEADERS).status_code, 502)
        self.assertNotIn("POST /rest/v1/rpc/pf_delete_report", [call[0] for call in self.calls])

    def test_storage_network_failures_are_explicit(self):
        def timeout(_request):
            raise httpx.ReadTimeout("slow")

        def unreachable(_request):
            raise httpx.ConnectError("down")
        for failure, status in [(timeout, 504), (unreachable, 502)]:
            with self.subTest(status=status), self.client(self.reserve_routes(failure)) as client:
                self.assertEqual(client.post("/v1/reports", headers=HEADERS, json=UPLOAD).status_code, status)

    def call(self, client, method, message):
        return client.post(f"{SERVICE}/{method}", headers=CONNECT, content=json.dumps(message))

    def test_rpc_upload_list_download_and_delete_match_rest(self):
        rows = [{"id": REPORT, "project_id": PROJECT, "kind": "pdf", "size_bytes": 1200, "sha256": SHA, "status": "ready", "created_at": DATE, "completed_at": DATE}] * 2
        routes = {**self.reserve_routes(),
                  "POST /rest/v1/rpc/pf_list_reports": self.answer(rows),
                  "POST /rest/v1/rpc/pf_complete_report": self.answer(READY),
                  "POST /rest/v1/rpc/pf_report_object": self.answer({"report": READY, "objectPath": PATH}),
                  f"POST /storage/v1/object/sign/reports/{PATH}": self.answer({"signedURL": f"/object/sign/reports/{PATH}?token=short"}),
                  "DELETE /storage/v1/object/reports": self.answer([])}
        with self.client(routes) as client:
            upload = self.call(client, "CreateReportUpload", {"projectId": PROJECT, "kind": "REPORT_KIND_PDF", "sizeBytes": 1200, "sha256": SHA}).json()
            completed = self.call(client, "CompleteReport", {"id": REPORT}).json()
            first = self.call(client, "ListReports", {"pageSize": 1, "projectId": PROJECT}).json()
            second = self.call(client, "ListReports", {"pageSize": 1, "projectId": PROJECT, "pageToken": first["nextPageToken"]})
            other_filter = self.call(client, "ListReports", {"pageSize": 1, "pageToken": first["nextPageToken"]})
            download = self.call(client, "CreateReportDownload", {"id": REPORT}).json()
            deleted = self.call(client, "DeleteReport", {"id": REPORT})
        self.assertEqual(upload["report"]["kind"], "REPORT_KIND_PDF")
        self.assertEqual(upload["report"]["status"], "REPORT_STATUS_PENDING")
        self.assertEqual(upload["contentType"], "application/pdf")
        self.assertTrue(upload["uploadUrl"].endswith("?token=signed"))
        self.assertEqual(completed["report"]["status"], "REPORT_STATUS_READY")
        self.assertEqual(first["reports"][0]["projectId"], PROJECT)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(other_filter.json()["code"], "invalid_argument")
        self.assertEqual(download["fileName"], "proteinforge-report.pdf")
        self.assertEqual(deleted.status_code, 200)

    def test_rpc_errors_use_stable_codes(self):
        quota = {"POST /rest/v1/rpc/pf_reserve_report": self.answer({"code": "PT413", "message": "Report quota exceeded"}, 400)}
        missing = {"POST /rest/v1/rpc/pf_complete_report": self.answer({"code": "PT409", "message": "Report upload missing"}, 400)}
        with self.client({**quota, **missing}) as client:
            unspecified = self.call(client, "CreateReportUpload", {"kind": "REPORT_KIND_UNSPECIFIED", "sizeBytes": 1, "sha256": SHA}).json()
            oversized = self.call(client, "CreateReportUpload", {"kind": "REPORT_KIND_JSON", "sizeBytes": report_config.MAX_REPORT_BYTES + 1, "sha256": SHA}).json()
            bad_project = self.call(client, "CreateReportUpload", {"projectId": "x", "kind": "REPORT_KIND_JSON", "sizeBytes": 1, "sha256": SHA}).json()
            exhausted = self.call(client, "CreateReportUpload", {"kind": "REPORT_KIND_JSON", "sizeBytes": 1, "sha256": SHA}).json()
            not_uploaded = self.call(client, "CompleteReport", {"id": REPORT}).json()
            page = self.call(client, "ListReports", {"pageSize": report_config.MAX_REPORT_PAGE_SIZE + 1}).json()
        self.assertEqual(unspecified, {"code": "invalid_argument", "message": "validation: kind"})
        self.assertEqual(oversized["code"], "invalid_argument")
        self.assertEqual(bad_project, {"code": "invalid_argument", "message": "validation: project_id"})
        self.assertEqual(exhausted, {"code": "resource_exhausted", "message": "report-quota-exceeded"})
        self.assertEqual(not_uploaded, {"code": "failed_precondition", "message": "report-upload-missing"})
        self.assertEqual(page, {"code": "invalid_argument", "message": "validation: page_size"})


if __name__ == "__main__":
    unittest.main()
