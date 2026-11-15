import unittest
import httpx
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.rate_limit import FixedWindowLimiter
from app.uniprot import MAX_FEATURE_DESCRIPTION, MAX_FEATURES_PER_KIND

ENTRY = {"primaryAccession": "P01308", "uniProtkbId": "INS_HUMAN", "entryType": "UniProtKB reviewed (Swiss-Prot)", "proteinDescription": {"recommendedName": {"fullName": {"value": "Insulin"}}}, "organism": {"scientificName": "Homo sapiens"}, "sequence": {"length": 3, "value": "ACD"}, "entryAudit": {"entryVersion": 284, "sequenceVersion": 1}, "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Test annotation"}]}], "uniProtKBCrossReferences": [{"database": "PDB", "id": "1TRZ", "properties": [{"key": "Method", "value": "X-ray"}, {"key": "Resolution", "value": "1.60 A"}, {"key": "Chains", "value": "A=90-110"}]}]}


class UniProtTests(unittest.TestCase):
    def client(self, response=None, requests_per_minute=0):
        def transport(request):
            if request.url.host == "project.supabase.co":
                return httpx.Response(200, json={"id": "owner", "email": "a@example.org"})
            self.assertEqual(request.url.host, "rest.uniprot.org")
            self.assertNotIn("Authorization", request.headers)
            self.assertNotIn("apikey", request.headers)
            if response:
                return response(request)
            return httpx.Response(200, json={"results": [ENTRY] * 11} if request.url.path.endswith("search") else ENTRY)
        settings = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", uniprot_requests_per_minute=requests_per_minute)
        return TestClient(create_app(settings, httpx.AsyncClient(transport=httpx.MockTransport(transport))))

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

    def test_positional_annotations_keep_only_exact_spans_inside_the_sequence(self):
        def at(start, end, modifier="EXACT"):
            return {"start": {"value": start, "modifier": "EXACT"}, "end": {"value": end, "modifier": modifier}}
        annotated = {**ENTRY, "features": [
            {"type": "Natural variant", "location": at(2, 2), "description": "in a disease", "alternativeSequence": {"originalSequence": "C", "alternativeSequences": ["S", "Y"]}},
            {"type": "Disulfide bond", "location": at(1, 3), "description": ""},
            {"type": "Active site", "location": at(2, 2), "description": "Proton donor"},
            {"type": "Binding site", "location": at(1, 2), "ligand": {"name": "Zn(2+)"}},
            {"type": "Domain", "location": at(1, 3, "OUTSIDE"), "description": "Uncertain end"},
            {"type": "Region", "location": at(2, 9), "description": "Past the sequence"},
            {"type": "Helix", "location": at(1, 3), "description": "Not a kept type"},
            {"type": "Site", "location": {"start": {"value": True, "modifier": "EXACT"}, "end": {"value": 2, "modifier": "EXACT"}}},
            "not a feature",
        ]}
        with self.client(lambda _: httpx.Response(200, json=annotated)) as client:
            record = client.get("/v1/uniprot/P01308", headers={"Authorization": "Bearer user-token"}).json()
        self.assertEqual(record["features"], [
            {"kind": "active-site", "description": "Proton donor", "begin": 2, "end": 2, "paired": False},
            {"kind": "binding-site", "description": "Zn(2+)", "begin": 1, "end": 2, "paired": False},
            {"kind": "disulfide", "description": "", "begin": 1, "end": 3, "paired": True},
            {"kind": "variant", "description": "C > S/Y; in a disease", "begin": 2, "end": 2, "paired": False},
        ])
        self.assertFalse(record["featuresTruncated"])

    def test_an_entry_without_annotations_has_none_and_crowded_kinds_are_bounded(self):
        crowded = {**ENTRY, "features": [{"type": "Natural variant", "location": {"start": {"value": 1, "modifier": "EXACT"}, "end": {"value": 1, "modifier": "EXACT"}}, "description": "x" * 900}] * (MAX_FEATURES_PER_KIND + 1)}
        with self.client() as client:
            plain = client.get("/v1/uniprot/P01308", headers={"Authorization": "Bearer user-token"}).json()
        with self.client(lambda _: httpx.Response(200, json=crowded)) as client:
            record = client.get("/v1/uniprot/P01308", headers={"Authorization": "Bearer user-token"}).json()
        self.assertEqual((plain["features"], plain["featuresTruncated"]), ([], False))
        self.assertEqual(len(record["features"]), MAX_FEATURES_PER_KIND)
        self.assertTrue(record["featuresTruncated"])
        self.assertEqual(len(record["features"][0]["description"]), MAX_FEATURE_DESCRIPTION)

    def test_rejects_inconsistent_public_data_and_translates_provider_errors(self):
        cases = [(httpx.Response(200, json={**ENTRY, "sequence": {"length": 3, "value": "AC"}}), 502), (httpx.Response(200, json={}), 502), (httpx.Response(404), 404), (httpx.Response(429), 429), (httpx.Response(503, text="private provider trace"), 502)]
        for response, expected in cases:
            with self.client(lambda _, r=response: r) as client:
                result = client.get("/v1/uniprot/P01308", headers={"Authorization": "Bearer user-token"})
                self.assertEqual(result.status_code, expected)
                self.assertNotIn("provider trace", result.text)

    def test_one_caller_cannot_flood_the_provider(self):
        with self.client(requests_per_minute=2) as client:
            headers = {"Authorization": "Bearer user-token"}
            statuses = [client.get("/v1/uniprot/P01308", headers=headers).status_code for _ in range(3)]
            refused = client.get("/v1/uniprot/search?query=insulin", headers=headers)
        self.assertEqual(statuses, [200, 200, 429])
        # Both routes share the caller's budget, and the refusal names no private value.
        self.assertEqual(refused.status_code, 429)
        self.assertIn("uniprot-rate-limit", refused.text)

    def test_a_malformed_query_does_not_spend_the_budget(self):
        with self.client(requests_per_minute=1) as client:
            headers = {"Authorization": "Bearer user-token"}
            self.assertEqual(client.get("/v1/uniprot/search?query=%20%20", headers=headers).status_code, 422)
            self.assertEqual(client.get("/v1/uniprot/P01308", headers=headers).status_code, 200)


class RateLimitTests(unittest.TestCase):
    def test_counts_each_caller_separately_and_reopens_after_the_window(self):
        limiter = FixedWindowLimiter(2, window_seconds=60.0)
        self.assertEqual([limiter.allow("a", 0.0), limiter.allow("a", 1.0), limiter.allow("a", 2.0)], [True, True, False])
        self.assertTrue(limiter.allow("b", 2.0))
        # A refused request does not extend the window, and the next window starts clean.
        self.assertTrue(limiter.allow("a", 61.0))
        self.assertTrue(limiter.allow("a", 62.0))
        self.assertFalse(limiter.allow("a", 63.0))

    def test_forgets_old_callers_and_an_unset_budget_allows_everything(self):
        limiter = FixedWindowLimiter(1, window_seconds=10.0, max_callers=2)
        for index in range(5):
            limiter.allow(f"caller-{index}", float(index))
        self.assertLessEqual(len(limiter._windows), 2)
        unlimited = FixedWindowLimiter(0)
        self.assertTrue(all(unlimited.allow("a", float(index)) for index in range(100)))
