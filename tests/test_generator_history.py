"""Generator history validation, workspace versions and equivalent REST / Connect / gRPC-Web writes."""
import copy
import hashlib
import json
import struct
import unittest
from pathlib import Path

import httpx

from app.generator_history import MAX_HISTORY_BYTES, MAX_RUN_BYTES, GeneratorHistorySnapshot
from app.projects import ProjectWrite, Workspace
from app.rpc import convert
from app.rpc.gen.proteinforge.project.v1.project_service_pb import CreateProjectRequest, CreateProjectResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "generator"


def export(name="default"):
    """Exact bytes of a real generator download (see fixtures/generator/README.md)."""
    return (FIXTURES / f"{name}-export.json").read_bytes().decode("utf-8")


def variant(seed, name="default"):
    """Shape-valid synthetic run: the real export with another seed, not a new search."""
    result = json.loads(export(name))
    result["parameters"]["seed"] = seed
    return json.dumps(result, indent=2)


def run(result_json=None, **changes):
    text = export() if result_json is None else result_json
    return {"id": hashlib.sha256(text.encode("utf-8")).hexdigest(), "origin": "local-generator", "recordedAt": "2026-10-27T09:30:00-05:00",
            "appBuild": None, "resultJson": text, "selection": {"arm": "guided", "index": 0}, **changes}


def history_document(runs=None):
    return {"kind": "proteinforge-generator-history", "schemaVersion": 1, "runs": [run()] if runs is None else runs}


def envelope(doc=None, text=None):
    text = text if text is not None else json.dumps(doc or history_document(), ensure_ascii=False, separators=(",", ":"))
    return {"schemaVersion": 1, "documentJson": text, "documentSha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def rejected(test, value):
    with test.assertRaises(ValueError):
        GeneratorHistorySnapshot.model_validate(value)


class GeneratorHistoryTests(unittest.TestCase):
    def test_real_exports_are_accepted_and_preserved_exactly(self):
        runs = [run(export("default")), run(export("maximum"), selection=None, appBuild="0123abc")]
        value = envelope(history_document(runs))
        snapshot = GeneratorHistorySnapshot.model_validate(value)
        self.assertEqual(snapshot.document_json, value["documentJson"])
        self.assertEqual(snapshot.run_count(), 2)
        stored = json.loads(snapshot.document_json)["runs"][1]["resultJson"]
        self.assertEqual(hashlib.sha256(stored.encode()).hexdigest(), "429e6cef00e312b470ec4f6f0d79f8703f3be0019eb14bd5d13e37bafd0ab5cd")

    def test_envelope_version_hash_and_extra_fields_are_rejected(self):
        value = envelope()
        for changed in [{**value, "schemaVersion": 2}, {**value, "documentSha256": "0" * 64}, {**value, "extra": 1},
                        envelope(text="{}"), envelope(text='{"kind":1,"kind":2}')]:
            with self.subTest(changed=sorted(changed)):
                rejected(self, changed)

    def test_document_and_run_shape(self):
        def result_with(change):
            result = json.loads(export())
            change(result)
            return json.dumps(result, indent=2)
        mutations = {
            "extra document field": lambda d: d.update(extra=True),
            "unknown kind": lambda d: d.update(kind="proteinforge-laboratory"),
            "boolean version": lambda d: d.update(schemaVersion=True),
            "no runs": lambda d: d.update(runs=[]),
            "eleven runs": lambda d: d.update(runs=[run(variant(seed)) for seed in range(1, 12)]),
            "duplicate run": lambda d: d.update(runs=[run(), run()]),
            "wrong id": lambda d: d["runs"][0].update(id="0" * 64),
            "external origin": lambda d: d["runs"][0].update(origin="design-service"),
            "time without zone": lambda d: d["runs"][0].update(recordedAt="2026-10-27T09:30:00"),
            "invalid build": lambda d: d["runs"][0].update(appBuild="main"),
            "extra run field": lambda d: d["runs"][0].update(notes=""),
            "selection outside arm": lambda d: d["runs"][0].update(selection={"arm": "guided", "index": 5}),
            "unknown arm": lambda d: d["runs"][0].update(selection={"arm": "best", "index": 0}),
            "boolean index": lambda d: d["runs"][0].update(selection={"arm": "guided", "index": False}),
            "non-finite number": lambda d: d.update(runs=[run(export().replace('"bestLoss": 0', '"bestLoss": NaN', 1))]),
            "unknown method": lambda d: d.update(runs=[run(result_with(lambda r: r.update(methodVersion="proteinforge-candidates-v2")))]),
            "seed zero": lambda d: d.update(runs=[run(result_with(lambda r: r["parameters"].update(seed=0)))]),
            "length over 300": lambda d: d.update(runs=[run(result_with(lambda r: r["parameters"].update(length=301)))]),
            "no active range": lambda d: d.update(runs=[run(result_with(lambda r: r["parameters"]["gravy"].update(enabled=False)))]),
            "lowercase candidate": lambda d: d.update(runs=[run(result_with(lambda r: r["guided"]["candidates"][0].update(sequence=r["guided"]["candidates"][0]["sequence"].lower())))]),
            "candidate length": lambda d: d.update(runs=[run(result_with(lambda r: r["guided"]["candidates"][0].update(sequence="ACDEF")))]),
            "six candidates": lambda d: d.update(runs=[run(result_with(lambda r: r["guided"]["candidates"].append(dict(r["reference"]["candidates"][0]))))]),
            "missing descriptor": lambda d: d.update(runs=[run(result_with(lambda r: r["guided"]["candidates"][0]["descriptors"].pop("gravy")))]),
        }
        for name, mutate in mutations.items():
            doc = copy.deepcopy(history_document())
            mutate(doc)
            with self.subTest(name):
                rejected(self, envelope(doc))

    def test_limits_count_utf8_bytes_and_never_truncate(self):
        text = json.dumps(history_document(), separators=(",", ":"))
        exact = text + " " * (MAX_HISTORY_BYTES - len(text.encode()))
        self.assertEqual(GeneratorHistorySnapshot.model_validate(envelope(text=exact)).document_json, exact)
        for over in [exact + " ", text + " " * 40_000]:
            rejected(self, envelope(text=over))
        result = export()
        padded = result + " " * (MAX_RUN_BYTES - len(result.encode()))
        self.assertEqual(GeneratorHistorySnapshot.model_validate(envelope(history_document([run(padded)]))).run_count(), 1)
        rejected(self, envelope(history_document([run(padded + " ")])))
        ten = envelope(history_document([run(variant(seed)) for seed in range(1, 11)]))
        self.assertEqual(GeneratorHistorySnapshot.model_validate(ten).run_count(), 10)

    def test_workspace_versions_keep_their_meaning(self):
        from test_laboratory import envelope as laboratory
        history, lab = envelope(), laboratory()
        accepted = [{"schemaVersion": 1}, {"schemaVersion": 2, "laboratory": lab}, {"schemaVersion": 3},
                    {"schemaVersion": 3, "laboratory": lab}, {"schemaVersion": 3, "generatorHistory": history},
                    {"schemaVersion": 3, "laboratory": lab, "generatorHistory": history}]
        for value in accepted:
            with self.subTest(accepted=sorted(value)):
                Workspace.model_validate(value)
        refused = [{"schemaVersion": 1, "generatorHistory": history}, {"schemaVersion": 2, "laboratory": lab, "generatorHistory": history},
                   {"schemaVersion": 2}, {"schemaVersion": 1, "laboratory": lab}, {"schemaVersion": 4}]
        for value in refused:
            with self.subTest(refused=sorted(value)), self.assertRaises(ValueError):
                Workspace.model_validate(value)
        dumped = Workspace.model_validate({"schemaVersion": 3}).model_dump(by_alias=True)
        self.assertNotIn("generatorHistory", dumped)
        self.assertNotIn("laboratory", dumped)

    def test_full_project_with_maximum_laboratory_and_history_fits_existing_limits(self):
        from test_laboratory import envelope as laboratory, laboratory_fixture
        from app.laboratory import MAX_LABORATORY_BYTES
        lab_text = json.dumps(laboratory_fixture())
        lab = laboratory(text=lab_text + " " * (MAX_LABORATORY_BYTES - len(lab_text.encode())))
        history_text = json.dumps(history_document([run(variant(seed)) for seed in range(1, 11)]), separators=(",", ":"))
        history = envelope(text=history_text + " " * (MAX_HISTORY_BYTES - len(history_text.encode())))
        project = ProjectWrite.model_validate({"name": "Limits", "activeSequence": "ACD", "workspace": {"schemaVersion": 3, "laboratory": lab, "generatorHistory": history}})
        self.assertLess(len(project.model_dump_json(by_alias=True).encode()), 2_000_000)


class GeneratorHistoryTransportTests(unittest.TestCase):
    def test_rest_connect_and_grpc_web_preserve_envelope_and_compact_preview(self):
        from test_rpc import RpcTests, DOCUMENT, WORKSPACE, RPC_WORKSPACE, PROJECTS, TOKEN, grpc_web_frames
        history = envelope(history_document([run(), run(export("maximum"), selection=None)]))
        workspace = {**WORKSPACE, "schemaVersion": 3, "generatorHistory": history}
        stored = {**DOCUMENT, "workspace": workspace}
        def data(request):
            self.assertEqual(json.loads(request.content)["p_workspace"], workspace)
            return httpx.Response(200, json=stored)
        helper = RpcTests()
        with helper.client(data) as client:
            rest = client.post("/v1/projects", headers=TOKEN, json={"name": "Study", "activeSequence": "ACD", "workspace": workspace})
            connect = helper.call(client, PROJECTS + "/CreateProject", {"name": "Study", "activeSequence": "ACD", "workspace": {**RPC_WORKSPACE, "schemaVersion": 3, "generatorHistory": history}}, TOKEN)
            request = CreateProjectRequest(name="Study", active_sequence="ACD", workspace=convert.workspace_message(Workspace.model_validate(workspace))).to_binary()
            web = client.post(PROJECTS + "/CreateProject", headers={**TOKEN, "Content-Type": "application/grpc-web+proto"}, content=struct.pack(">BI", 0, len(request)) + request)
        self.assertEqual(rest.status_code, 201, rest.text)
        self.assertEqual(rest.json()["workspace"]["generatorHistory"], history)
        self.assertEqual(connect.status_code, 200, connect.text)
        self.assertEqual(connect.json()["project"]["workspace"]["generatorHistory"]["documentJson"], history["documentJson"])
        self.assertEqual(connect.json()["project"]["summary"]["preview"]["generatorRuns"], 2)
        frames = grpc_web_frames(web.content)
        self.assertIn(b"grpc-status:0", frames[-1][1].replace(b" ", b""))
        response = CreateProjectResponse.from_binary(frames[0][1]).project
        self.assertEqual(response.workspace.generator_history.document_sha256, history["documentSha256"])
        self.assertEqual(response.summary.preview.generator_runs, 2)
        self.assertNotIn("documentJson", json.dumps(connect.json()["project"]["summary"]))

    def test_invalid_or_misversioned_history_never_reaches_storage(self):
        from test_rpc import RpcTests, WORKSPACE, RPC_WORKSPACE, PROJECTS, TOKEN
        bad = {**envelope(), "documentSha256": "0" * 64}
        helper = RpcTests()
        cases = [(3, bad), (2, envelope()), (1, envelope())]
        with helper.client(lambda _: self.fail("Invalid history reached storage")) as client:
            for version, history in cases:
                with self.subTest(version=version):
                    extra = {"laboratory": __import__("test_laboratory").envelope()} if version == 2 else {}
                    rest = client.post("/v1/projects", headers=TOKEN, json={"name": "Study", "workspace": {**WORKSPACE, **extra, "schemaVersion": version, "generatorHistory": history}})
                    rpc = helper.call(client, PROJECTS + "/CreateProject", {"name": "Study", "workspace": {**RPC_WORKSPACE, **extra, "schemaVersion": version, "generatorHistory": history}}, TOKEN)
                    self.assertEqual(rest.status_code, 422)
                    self.assertIn("workspace", rest.json()["detail"])
                    self.assertEqual(rpc.json()["code"], "invalid_argument")
                    self.assertNotIn(history["documentJson"], rest.text + rpc.text)

    def test_newer_stored_version_has_its_own_code_but_remains_a_conflict(self):
        from test_rpc import RpcTests, WORKSPACE, RPC_WORKSPACE, PROJECT, DATE, PROJECTS, TOKEN
        from app.rpc.gen.proteinforge.project.v1.project_service_pb import UpdateProjectRequest
        helper = RpcTests()
        for message, code in [("Project requires current client", "project-requires-current-client"), ("Project changed", "project-conflict")]:
            storage = lambda _request, message=message: httpx.Response(409, json={"code": "PT409", "message": message})
            with self.subTest(code=code), helper.client(storage) as client:
                rest = client.put(f"/v1/projects/{PROJECT}", headers=TOKEN, json={"name": "Study", "workspace": WORKSPACE, "expectedUpdatedAt": DATE})
                connect = helper.call(client, PROJECTS + "/UpdateProject", {"id": PROJECT, "name": "Study", "workspace": RPC_WORKSPACE, "expectedUpdateTime": DATE}, TOKEN)
                request = UpdateProjectRequest(id=PROJECT, name="Study", workspace=convert.workspace_message(Workspace.model_validate(WORKSPACE)), expected_update_time=convert.timestamp(DATE)).to_binary()
                web = client.post(PROJECTS + "/UpdateProject", headers={**TOKEN, "Content-Type": "application/grpc-web+proto"}, content=struct.pack(">BI", 0, len(request)) + request)
            self.assertEqual(rest.status_code, 409)
            self.assertEqual(rest.json()["type"].rsplit("/", 1)[1], code)
            self.assertEqual(connect.status_code, 409)
            self.assertEqual(connect.json(), {"code": "aborted", "message": code})
            trailer = web.content.replace(b" ", b"")
            self.assertIn(b"grpc-status:10", trailer)
            self.assertIn(f"grpc-message:{code}".encode(), trailer)

    def test_listing_reports_only_run_counts_and_rejects_impossible_counts(self):
        from test_rpc import RpcTests, PROJECT, DATE, PROJECTS, TOKEN
        helper = RpcTests()
        def listing(runs):
            preview = {"hasLaboratory": False, "laboratoryVariants": 0, "generatorRuns": runs, "sequenceLength": 0, "sequencePrefix": "", "excluded": 0}
            return lambda _request: httpx.Response(200, json=[{"id": PROJECT, "name": "Study", "status": "draft", "created_at": DATE, "updated_at": DATE, "preview": preview}])
        with helper.client(listing(3)) as client:
            rest = client.get("/v1/projects", headers=TOKEN)
            rpc = helper.call(client, PROJECTS + "/ListProjects", {}, TOKEN)
        self.assertEqual(rest.json()["items"][0]["preview"]["generatorRuns"], 3)
        self.assertEqual(rpc.json()["projects"][0]["preview"]["generatorRuns"], 3)
        with helper.client(listing(11)) as client:
            self.assertEqual(client.get("/v1/projects", headers=TOKEN).status_code, 502)

    def test_rpc_message_limit_is_not_raised(self):
        from test_rpc import RpcTests, PROJECTS, TOKEN
        helper = RpcTests()
        body = b"\x00" * 2_000_001
        with helper.client(lambda _: self.fail("Oversized message reached storage")) as client:
            web = client.post(PROJECTS + "/CreateProject", headers={**TOKEN, "Content-Type": "application/grpc-web+proto"}, content=struct.pack(">BI", 0, len(body)) + body)
        trailer = web.content.replace(b" ", b"")
        self.assertNotIn(b"grpc-status:0", trailer)
        self.assertIn(b"grpc-status:8", trailer)


if __name__ == "__main__":
    unittest.main()
