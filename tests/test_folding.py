"""Folding uses a simulated public provider and never sends credentials to it."""

import hashlib
import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.folding import FoldingGate, predict
from app.folding_config import FOLDING_URL, MAX_FOLDING_BYTES
from app.folding_models import FoldRequest, validate_pdb
from app.main import create_app

SEQUENCE = "ACD"
TOKEN = {"Authorization": "Bearer caller-secret"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1", **TOKEN}


def pdb_for(sequence=SEQUENCE):
    names = {"A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU"}
    lines = ["TITLE     PUBLIC TEST PREDICTION"]
    for index, residue in enumerate(sequence, 1):
        for offset, atom in enumerate(("N", "CA", "C", "O")):
            lines.append(f"ATOM  {(index-1)*4+offset+1:5d} {atom:>4} {names[residue]} A{index:4d}    {float(index):8.3f}{float(offset):8.3f}{0.0:8.3f}{1.0:6.2f}{0.9:6.2f}           C  ")
    return "\n".join(lines) + "\nTER\nEND\n"


class FoldingTests(unittest.TestCase):
    def client(self, provider=None, enabled=True, auth_delay=0):
        self.sent = []
        async def transport(request):
            if request.url.host == "project.supabase.co":
                await asyncio.sleep(auth_delay)
                return httpx.Response(200, json={"id": "00000000-0000-4000-8000-000000000001", "email": "a@example.org"})
            self.assertEqual(str(request.url), FOLDING_URL)
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("apikey", request.headers)
            self.sent.append(request.content)
            result = provider(request) if provider else httpx.Response(200, text=pdb_for(), headers={"Content-Type": "chemical/x-pdb"})
            return await result if inspect.isawaitable(result) else result
        return TestClient(create_app(Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", folding_enabled=enabled), httpx.AsyncClient(transport=httpx.MockTransport(transport))))

    def post(self, client, **body):
        return client.post("/v1/predictions/fold", headers=TOKEN, json={"sequence": SEQUENCE, "consentToSendSequence": True, **body})

    def test_exact_result_and_absent_metrics(self):
        with self.client() as client:
            response = self.post(client)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["sequence"], SEQUENCE)
        self.assertEqual(result["pdb"], pdb_for())
        self.assertEqual(result["pdbSha256"], hashlib.sha256(pdb_for().encode()).hexdigest())
        self.assertEqual(result["sequenceSha256"], hashlib.sha256(SEQUENCE.encode()).hexdigest())
        self.assertTrue(all(result[key] is None for key in ("modelRevision", "confidence", "ptm", "pae")))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.sent, [SEQUENCE.encode()])

    def test_configuration_auth_consent_and_validation_do_not_call_provider(self):
        self.assertFalse(load_settings({}).folding_enabled)
        self.assertTrue(load_settings({"FOLDING_ENABLED": "enabled"}).folding_enabled)
        with self.client(enabled=False) as client:
            self.assertEqual(self.post(client).status_code, 503)
        with self.client() as client:
            self.assertEqual(client.post("/v1/predictions/fold", json={"sequence": SEQUENCE, "consentToSendSequence": True}).status_code, 401)
            self.assertEqual(self.post(client, consentToSendSequence=False).status_code, 403)
            for body in ({"sequence": "AXD"}, {"sequence": "AC"}, {"sequence": "A" * 121}, {"sequence": "acd"}, {"url": "https://invalid.example"}, {"consentToSendSequence": "true"}):
                self.assertEqual(self.post(client, **body).status_code, 422)
        self.assertEqual(self.sent, [])

    def test_provider_failures_and_invalid_structures_are_explicit(self):
        cases = [
            (httpx.Response(429), 429), (httpx.Response(503), 502),
            (httpx.Response(200, text="<html>not a structure</html>", headers={"Content-Type": "text/html"}), 502),
            (httpx.Response(200, text=pdb_for("ACE")), 502),
            (httpx.Response(200, text=pdb_for().replace("  CA ", "  ZZ ")), 502),
            (httpx.Response(200, text="x" * (MAX_FOLDING_BYTES + 1)), 502),
            (httpx.Response(200, text=pdb_for(), headers={"Content-Encoding": "identity, identity"}), 502),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected), self.client(lambda _, r=response: r) as client:
                result = self.post(client)
                self.assertEqual(result.status_code, expected, result.text)
                self.assertNotIn(SEQUENCE, result.text)
                self.assertNotIn("caller-secret", result.text)

    def test_timeout_is_explicit_and_admission_is_released(self):
        def timeout(request):
            raise httpx.ReadTimeout("private upstream body", request=request)
        with self.client(timeout) as client:
            result = self.post(client)
            self.assertEqual(result.status_code, 504)
            self.assertNotIn("private upstream", result.text)
            self.assertFalse(client.app.state.folding_gate._active)

    def test_rest_and_connect_share_identity_budget(self):
        with self.client() as client:
            result = client.post("/proteinforge.folding.v1.FoldingService/Predict", headers=CONNECT, json={"sequence": SEQUENCE, "consentToSendSequence": True})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["pdb"], pdb_for())
            self.assertEqual(self.post(client).status_code, 429)
        self.assertEqual(len(self.sent), 1)

    def test_rpc_deadline_during_identity_never_calls_provider(self):
        for timeout in ('0', '1'):
            with self.subTest(timeout=timeout), self.client(auth_delay=.1) as client:
                response = client.post('/proteinforge.folding.v1.FoldingService/Predict',
                    headers={**CONNECT, 'Connect-Timeout-Ms': timeout},
                    json={'sequence': SEQUENCE, 'consentToSendSequence': True})
                self.assertEqual(response.json().get('code'), 'deadline_exceeded', response.text)
                self.assertEqual(self.sent, [])
                self.assertFalse(client.app.state.folding_gate._active)

    def test_rpc_deadline_during_provider_releases_admission(self):
        async def slow_provider(_request):
            await asyncio.sleep(.1)
            return httpx.Response(200, text=pdb_for(), headers={'Content-Type': 'chemical/x-pdb'})
        with self.client(slow_provider) as client:
            response = client.post('/proteinforge.folding.v1.FoldingService/Predict',
                headers={**CONNECT, 'Connect-Timeout-Ms': '10'},
                json={'sequence': SEQUENCE, 'consentToSendSequence': True})
            self.assertEqual(response.json().get('code'), 'deadline_exceeded', response.text)
            self.assertEqual(len(self.sent), 1)
            self.assertFalse(client.app.state.folding_gate._active)

    def test_overall_deadline_and_cancellation_release_admission(self):
        async def run():
            state = SimpleNamespace(settings=Settings(folding_enabled=True), folding_gate=FoldingGate(), http_client=None)
            body = FoldRequest(sequence=SEQUENCE, consent_to_send_sequence=True)
            async def stalled(*_args):
                await asyncio.sleep(60)
            with patch('app.folding._exchange', stalled), patch('app.folding.FOLDING_TIMEOUT_SECONDS', .01):
                from fastapi import HTTPException
                with self.assertRaises(HTTPException) as failure:
                    await predict(state, body, 'a')
                self.assertEqual(failure.exception.status_code, 504)
                self.assertFalse(state.folding_gate._active)
            with patch('app.folding._exchange', stalled):
                task = asyncio.create_task(predict(state, body, 'b'))
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(state.folding_gate._active)
        asyncio.run(run())

    def test_gate_is_atomic_per_identity_and_process(self):
        gate = FoldingGate()
        self.assertTrue(gate.acquire("a", 100))
        self.assertFalse(gate.acquire("b", 100))
        gate.release()
        self.assertFalse(gate.acquire("a", 100))
        for caller in ("b", "c"):
            self.assertTrue(gate.acquire(caller, 100))
            gate.release()
        self.assertFalse(gate.acquire("d", 100))
        self.assertTrue(gate.acquire("a", 161))
        gate.release()

    def test_pdb_validation_rejects_nonfinite_duplicate_and_multiple_models(self):
        good = pdb_for()
        validate_pdb(good, SEQUENCE)
        for text in (good.replace("   1.000", "     nan", 1), good + good, "MODEL        1\n" + good + "MODEL        2\n", good.replace(" CYS ", " GLU ")):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                validate_pdb(text, SEQUENCE)


if __name__ == "__main__":
    unittest.main()
