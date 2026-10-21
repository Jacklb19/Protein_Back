"""Portable laboratory validation and equivalent REST / binary gRPC-Web writes."""
import copy
import hashlib
import json
import struct
import unittest

import httpx

from app.laboratory import LaboratorySnapshot, MAX_LABORATORY_BYTES
from app.projects import ProjectWrite, Workspace
from app.rpc import convert
from app.rpc.gen.proteinforge.project.v1.project_service_pb import CreateProjectRequest, CreateProjectResponse


def laboratory_fixture():
    key = "a" * 64
    return {"kind": "proteinforge-laboratory", "schemaVersion": 1,
            "source": {"type": "example", "id": "1ubq-colab-20261021-v1"},
            "state": {"activeId": key, "selected": 1, "drafts": {key: {"sequence": "ACD", "protectedPositions": [1]}}}}


def envelope(doc=None, text=None):
    text = text if text is not None else json.dumps(doc or laboratory_fixture(), ensure_ascii=False)
    return {"schemaVersion": 1, "documentJson": text, "documentSha256": hashlib.sha256(text.encode()).hexdigest()}


class LaboratoryTests(unittest.TestCase):
    def test_version_hash_and_corrupt_source_are_rejected(self):
        lab = envelope()
        self.assertEqual(LaboratorySnapshot.model_validate(lab).document_json, lab["documentJson"])
        for changed in [{**lab, "schemaVersion": 2}, {**lab, "documentSha256": "0" * 64}, envelope(text="{}"), envelope(text='{"kind":1,"kind":2}')]:
            with self.subTest(changed=list(changed)), self.assertRaises(ValueError):
                LaboratorySnapshot.model_validate(changed)
        for version, value in [(1, lab), (2, None)]:
            with self.assertRaises(ValueError):
                Workspace.model_validate({"schemaVersion": version, "laboratory": value})
        Workspace.model_validate({"schemaVersion": 1})

    def test_drafts_ranges_active_sequence_and_variant_limits(self):
        mutations = [lambda d: d["state"].update(activeId="missing"), lambda d: d["state"].update(selected=3),
                     lambda d: d["state"].update(selected=True), lambda d: d["state"]["drafts"]["a"*64].update(sequence="AXD"),
                     lambda d: d["state"]["drafts"]["a"*64].update(sequence="A"*121),
                     lambda d: d["state"]["drafts"]["a"*64].update(protectedPositions=[1,1]),
                     lambda d: d["state"]["drafts"]["a"*64].update(protectedPositions=[True]),
                     lambda d: d["state"]["drafts"]["a"*64].update(protectedPositions=[3]),
                     lambda d: d["state"].update(drafts={str(i)*64:{"sequence":"ACD","protectedPositions":[]} for i in range(4)}),
                     lambda d: d.update(source={"type":"archive","base64":"AAAA","sha256":"0"*64})]
        for mutate in mutations:
            doc=copy.deepcopy(laboratory_fixture()); mutate(doc)
            with self.assertRaises(ValueError): LaboratorySnapshot.model_validate(envelope(doc))

    def test_limit_counts_utf8_bytes_and_preserves_exact_document(self):
        text=json.dumps(laboratory_fixture())
        exact=text+' '*(MAX_LABORATORY_BYTES-len(text.encode()))
        self.assertEqual(LaboratorySnapshot.model_validate(envelope(text=exact)).document_json,exact)
        for text in [exact+' ', text+'\u2003'*400_000]:
            with self.assertRaises(ValueError): LaboratorySnapshot.model_validate(envelope(text=text))

    def test_rest_and_grpc_web_preserve_envelope_and_compact_preview(self):
        from test_rpc import RpcTests, DOCUMENT, WORKSPACE, PROJECTS, TOKEN, grpc_web_frames
        lab=envelope(); workspace={**WORKSPACE,"schemaVersion":2,"laboratory":lab}
        document={**DOCUMENT,"workspace":workspace}
        def data(request):
            body=json.loads(request.content)
            self.assertEqual(body["p_workspace"],workspace)
            return httpx.Response(200,json=document)
        helper=RpcTests()
        with helper.client(data) as client:
            rest=client.post('/v1/projects',headers=TOKEN,json={"name":"Study","activeSequence":"ACD","workspace":workspace})
            self.assertEqual(rest.status_code,201,rest.text)
            model=Workspace.model_validate(workspace)
            request=CreateProjectRequest(name="Study",active_sequence="ACD",workspace=convert.workspace_message(model)).to_binary()
            web=client.post(PROJECTS+'/CreateProject',headers={**TOKEN,"Content-Type":"application/grpc-web+proto"},content=struct.pack('>BI',0,len(request))+request)
        frames=grpc_web_frames(web.content)
        self.assertIn(b'grpc-status:0',frames[-1][1].replace(b' ',b''))
        response=CreateProjectResponse.from_binary(frames[0][1]).project
        self.assertEqual(response.workspace.laboratory.document_json,lab['documentJson'])
        self.assertTrue(response.summary.preview.has_laboratory)
        self.assertEqual(response.summary.preview.laboratory_variants,1)
        self.assertNotIn('documentJson',str(response.summary))

    def test_invalid_envelope_never_reaches_storage_on_rest_or_rpc(self):
        from test_rpc import RpcTests, WORKSPACE, RPC_WORKSPACE, TOKEN, PROJECTS
        helper=RpcTests();lab={**envelope(),'documentSha256':'0'*64}
        with helper.client(lambda _:self.fail('Invalid laboratory reached storage')) as client:
            response=client.post('/v1/projects',headers=TOKEN,json={"name":"Study","workspace":{**WORKSPACE,"schemaVersion":2,"laboratory":lab}})
            rpc=helper.call(client,PROJECTS+'/CreateProject',{"name":"Study","workspace":{**RPC_WORKSPACE,"schemaVersion":2,"laboratory":lab}},TOKEN)
        self.assertEqual(response.status_code,422)
        self.assertEqual(rpc.json()['code'],'invalid_argument')
        self.assertNotIn(lab['documentJson'],response.text+rpc.text)
