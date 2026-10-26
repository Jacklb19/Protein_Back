"""Authenticated mapping on REST/Connect/gRPC-Web, with all outbound transport simulated."""
from copy import deepcopy
import gzip
import hashlib
import json
import struct
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rpc.gen.proteinforge.reference.v1.reference_service_pb import GetResidueMappingRequest, GetResidueMappingResponse
from app.sifts import normalise_mapping
from app.sifts_cache import SiftsCache
from test_sifts import XML, LISTING, RawStream
from test_uniprot import ENTRY

# Public complete P01308 SV1 sequence, also used by the frontend's curated reference.
SEQUENCE = 'MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN'
REFERENCE = '/proteinforge.reference.v1.ReferenceService/GetResidueMapping'
REST = '/v1/pdb/1TRZ/residue-mapping?accession=P01308&sequence_version=1'
MESSAGE = {'pdbId': '1TRZ', 'accession': 'P01308', 'sequenceVersion': 1}
TOKEN = {'Authorization': 'Bearer user-token'}
CONNECT = {**TOKEN, 'Content-Type': 'application/json', 'Connect-Protocol-Version': '1'}


class MappingServiceTests(unittest.TestCase):
    def client(self, *, entry=None, source=None, budget=20, private_defaults=False):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            if request.url.host == 'auth.supabase.invalid':
                if request.headers.get('Authorization') != 'Bearer user-token':
                    return httpx.Response(401)
                return httpx.Response(200, json={'id': 'owner', 'email': 'owner@example.org'})
            for header in ('Authorization', 'apikey', 'Cookie'):
                self.assertNotIn(header, request.headers)
            if request.url.host == 'rest.uniprot.org':
                self.assertEqual(request.url.path, '/uniprotkb/P01308.json')
                record = deepcopy(ENTRY)
                record['sequence'] = {'length': len(SEQUENCE), 'value': SEQUENCE}
                if entry:
                    entry(record)
                return httpx.Response(200, json=record)
            self.assertIn(request.url.host, ('ftp.ebi.ac.uk', 'www.ebi.ac.uk'))
            if source:
                return source(request)
            body = gzip.compress(XML) if request.url.host == 'ftp.ebi.ac.uk' else json.dumps(LISTING).encode()
            return httpx.Response(200, stream=RawStream([body]))
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                headers={'apikey': 'private-default', 'Authorization': 'Bearer private-default'} if private_defaults else {},
                                cookies={'session': 'private-default'} if private_defaults else None)
        settings = Settings(supabase_url='https://auth.supabase.invalid', supabase_anon_key='public-key',
                            uniprot_requests_per_minute=budget)
        return TestClient(create_app(settings, http))

    def test_rest_and_rpc_bind_the_complete_reference_without_forwarding_credentials(self):
        with self.client(private_defaults=True) as client:
            rest = client.get(REST, headers=TOKEN)
            rpc = client.post(REFERENCE, headers=CONNECT, json=MESSAGE)
        self.assertEqual(rest.status_code, 200, rest.text)
        self.assertEqual(rpc.status_code, 200, rpc.text)
        self.assertEqual(len(rest.json()['mapping']['rows']), 8)
        self.assertEqual(len(rpc.json()['mapping']['rows']), 8)
        self.assertEqual(rest.json()['referenceSequenceSha256'], hashlib.sha256(SEQUENCE.encode('ascii')).hexdigest())
        self.assertEqual(rpc.json()['referenceSequenceSha256'], rest.json()['referenceSequenceSha256'])
        self.assertNotIn(SEQUENCE, rpc.text)
        self.assertEqual(rest.headers['Cache-Control'], 'no-store')
        # SIFTS is cached by public PDB, but each request rechecks UniProt and spends its budget.
        self.assertEqual(sum(request.url.host == 'ftp.ebi.ac.uk' for request in self.requests), 1)
        self.assertEqual(sum(request.url.host == 'rest.uniprot.org' for request in self.requests), 2)

    def test_grpc_web_returns_typed_rows_with_optional_author_identity(self):
        message = GetResidueMappingRequest(pdb_id='1TRZ', accession='P01308', sequence_version=1).to_binary()
        with self.client() as client:
            response = client.post(REFERENCE, headers={**TOKEN, 'Content-Type': 'application/grpc-web+proto', 'X-Grpc-Web': '1'},
                                   content=struct.pack('>BI', 0, len(message)) + message)
        size = struct.unpack('>I', response.content[1:5])[0]
        result = GetResidueMappingResponse.from_binary(response.content[5:5 + size])
        self.assertEqual(result.mapping.rows[0].auth_seq_id, '1')
        self.assertEqual(result.reference_sequence_version, 1)
        self.assertIn(b'grpc-status:0', response.content[5 + size:].replace(b' ', b''))

    def test_authentication_validation_and_shared_budget_precede_provider_work(self):
        with self.client(budget=1) as client:
            self.assertEqual(client.get(REST).status_code, 401)
            self.assertEqual(client.post(REFERENCE, headers={'Content-Type': 'application/json'}, json=MESSAGE).status_code, 401)
            bad = client.post(REFERENCE, headers=CONNECT, json={**MESSAGE, 'sequenceVersion': 0})
            self.assertEqual(bad.json()['code'], 'invalid_argument')
            self.assertFalse(any(request.url.host != 'auth.supabase.invalid' for request in self.requests))
            self.assertEqual(client.get(REST, headers=TOKEN).status_code, 200)
            refused = client.get('/v1/uniprot/P01308', headers=TOKEN)
            self.assertEqual(refused.status_code, 429)
            self.assertIn('uniprot-rate-limit', refused.text)

    def test_current_reference_version_and_mapped_residues_are_required(self):
        for change, code in ((lambda record: record['entryAudit'].update(sequenceVersion=2), 'sifts-reference-version'),
                             (lambda record: record['sequence'].update(value=SEQUENCE[:89] + 'A' + SEQUENCE[90:]), 'sifts-reference-mismatch')):
            with self.subTest(code=code), self.client(entry=change) as client:
                response = client.post(REFERENCE, headers=CONNECT, json=MESSAGE)
                self.assertEqual(response.json(), {'code': 'failed_precondition', 'message': code})

    def test_absent_mapping_is_distinct_from_unavailable_or_missing_sources(self):
        with self.client(source=lambda _: httpx.Response(404, stream=RawStream([b'private-provider']))) as client:
            response = client.post(REFERENCE, headers=CONNECT, json=MESSAGE)
            self.assertEqual(response.json(), {'code': 'not_found', 'message': 'sifts-not-found'})
        with self.client() as client:
            empty = normalise_mapping(XML, LISTING, '1TRZ').model_copy(update={'rows': ()})
            client.app.state.sifts_cache.put(empty)
            response = client.get(REST, headers=TOKEN)
            self.assertEqual(response.json()['mapping']['rows'], [])

    def test_complete_response_is_refused_instead_of_truncated(self):
        with self.client() as client, patch('app.residue_mapping.MAPPING_RESPONSE_MAX_BYTES', 100):
            response = client.post(REFERENCE, headers=CONNECT, json=MESSAGE)
            self.assertEqual(response.json(), {'code': 'resource_exhausted', 'message': 'sifts-response-too-large'})
            self.assertNotIn('rows', response.text)
        with self.client() as client, patch('app.rpc.services.MAPPING_RESPONSE_MAX_BYTES', 100):
            response = client.post(REFERENCE, headers=CONNECT, json=MESSAGE)
            self.assertEqual(response.json(), {'code': 'resource_exhausted', 'message': 'sifts-response-too-large'})

    def test_cache_never_substitutes_for_a_fresh_sequence_version_check(self):
        version = [1]
        with self.client(entry=lambda record: record['entryAudit'].update(sequenceVersion=version[0])) as client:
            self.assertEqual(client.get(REST, headers=TOKEN).status_code, 200)
            version[0] = 2
            response = client.post(REFERENCE, headers=CONNECT, json=MESSAGE)
            self.assertEqual(response.json(), {'code': 'failed_precondition', 'message': 'sifts-reference-version'})
            self.assertEqual(sum(request.url.host == 'ftp.ebi.ac.uk' for request in self.requests), 1)

    def test_overall_timeout_closes_the_source_stream(self):
        stream = RawStream([gzip.compress(XML)], delay=.1)
        with self.client(source=lambda _: httpx.Response(200, stream=stream)) as client, patch('app.residue_mapping.MAPPING_TIMEOUT_SECONDS', .01):
            response = client.get(REST, headers=TOKEN)
            self.assertEqual(response.status_code, 504)
            self.assertIn('sifts-timeout', response.text)
            self.assertTrue(stream.closed)


class PublicCacheTests(unittest.TestCase):
    def test_cached_public_documents_are_bounded_expire_and_cannot_be_mutated(self):
        mapping = normalise_mapping(XML, LISTING, '1TRZ')
        size = len(mapping.model_dump_json(by_alias=True).encode('utf-8'))
        cache = SiftsCache(max_bytes=size + 1, ttl_seconds=10)
        with patch('app.sifts_cache.monotonic', return_value=0):
            cache.put(mapping)
            retrieved = cache.get('1TRZ')
            self.assertEqual(retrieved, mapping)
            self.assertIsNot(retrieved, mapping)
            cache.put(mapping.model_copy(update={'pdb_id': '2ABC'}))
            self.assertIsNone(cache.get('1TRZ'))
            self.assertIsNotNone(cache.get('2ABC'))
        with patch('app.sifts_cache.monotonic', return_value=11):
            self.assertIsNone(cache.get('2ABC'))
        tiny = SiftsCache(max_bytes=1)
        tiny.put(mapping)
        self.assertIsNone(tiny.get('1TRZ'))
