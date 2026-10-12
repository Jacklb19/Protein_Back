import unittest
import httpx
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

ENTRY = {"primaryAccession": "P01308", "uniProtkbId": "INS_HUMAN", "entryType": "UniProtKB reviewed (Swiss-Prot)", "proteinDescription": {"recommendedName": {"fullName": {"value": "Insulin"}}}, "organism": {"scientificName": "Homo sapiens"}, "sequence": {"length": 3, "value": "ACD"}, "entryAudit": {"entryVersion": 284, "sequenceVersion": 1}, "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Test annotation"}]}], "uniProtKBCrossReferences": [{"database": "PDB", "id": "1TRZ", "properties": [{"key": "Method", "value": "X-ray"}, {"key": "Resolution", "value": "1.60 A"}, {"key": "Chains", "value": "A=90-110"}]}]}


class UniProtTests(unittest.TestCase):
    def client(self, response=None):
        def transport(request):
            if request.url.host == "project.supabase.co":
                return httpx.Response(200, json={"id": "owner", "email": "a@example.org"})
            self.assertEqual(request.url.host, "rest.uniprot.org")
            self.assertNotIn("Authorization", request.headers)
            self.assertNotIn("apikey", request.headers)
            if response:
                return response(request)
            return httpx.Response(200, json={"results": [ENTRY] * 11} if request.url.path.endswith("search") else ENTRY)
        return TestClient(create_app(Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key"), httpx.AsyncClient(transport=httpx.MockTransport(transport))))

    def test_requires_identity_and_bounds_queries_and_accessions(self):
        with self.client() as client:
            self.assertEqual(client.get("/v1/uniprot/search?query=insulin").status_code, 401)
            for path in ["/v1/uniprot/search?query=x", "/v1/uniprot/search?query=%20%20", "/v1/uniprot/not-an-accession"]:
                self.assertEqual(client.get(path, headers={"Authorization": "Bearer user-token"}).status_code, 422)

    def test_search_is_bounded_and_annotations_have_provenance(self):
        with self.client() as client:
            result = client.get("/v1/uniprot/search?query=insulin", headers={"Authorization": "Bearer user-token"}).json()
            record = client.get("/v1/uniprot/P01308", headers={"Authorization": "Bearer user-token"}).json()
        self.assertEqual(len(result["items"]), 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(record["entryVersion"], 284)
        self.assertEqual(record["structures"][0]["chains"], "A=90-110")
        self.assertEqual(record["sequence"], "ACD")
        self.assertTrue(record["retrievedAt"])

    def test_rejects_inconsistent_public_data_and_translates_provider_errors(self):
        cases = [(httpx.Response(200, json={**ENTRY, "sequence": {"length": 3, "value": "AC"}}), 502), (httpx.Response(200, json={}), 502), (httpx.Response(404), 404), (httpx.Response(429), 429), (httpx.Response(503, text="private provider trace"), 502)]
        for response, expected in cases:
            with self.client(lambda _, r=response: r) as client:
                result = client.get("/v1/uniprot/P01308", headers={"Authorization": "Bearer user-token"})
                self.assertEqual(result.status_code, expected)
                self.assertNotIn("provider trace", result.text)
