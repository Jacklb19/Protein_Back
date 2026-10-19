"""Execute migrations, policies and concurrent writes in actual disposable PostgreSQL.

Use scripts/test-postgres.ps1. The bootstrap emulates auth.uid(), not Supabase Auth/PostgREST.
"""
import concurrent.futures
import json
import os
import subprocess
import unittest
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

DATABASE = os.environ.get("PF_TEST_DATABASE_URL", "")
PSQL = os.environ.get("PF_PSQL", "psql")


@unittest.skipUnless(DATABASE, "Disposable local PostgreSQL not configured; run scripts/test-postgres.ps1")
class PostgresSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlparse(DATABASE)
        if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/pf_security_test":
            raise RuntimeError("Refusing to run database checks outside disposable local pf_security_test")

    def sql(self, sql, user=None, role="authenticated", check=True):
        prefix = f"set role {role}; set request.jwt.claim.sub = '{user or ''}';" if user is not None or role == "anon" else ""
        process = subprocess.run([PSQL, DATABASE, "-X", "-v", "ON_ERROR_STOP=1", "-Atq", "-c", prefix + sql], capture_output=True, text=True)
        if check:
            self.assertEqual(process.returncode, 0, process.stderr)
        return process

    def setUp(self):
        self.owner, self.other = str(uuid4()), str(uuid4())
        self.sql(f"insert into auth.users(id) values ('{self.owner}'), ('{self.other}');")
        self.snapshot = '{"schemaVersion":1,"profileWindow":9,"descriptors":null,"alignment":{"first":"ACD","second":"ACD","matrix":"BLOSUM62","mode":"global","result":null}}'
        result = self.sql(f"select public.pf_save_project(null,'Study','ACD','{self.snapshot}',null);", self.owner)
        self.project = json.loads(result.stdout.strip())
        self.id = self.project["id"]
        self.version = self.project["updatedAt"]

    def test_owner_can_create_open_update_and_delete_snapshot_with_cascade(self):
        opened = self.sql(f"select public.pf_project_document('{self.id}');", self.owner)
        self.assertEqual(json.loads(opened.stdout)["workspace"]["alignment"]["first"], "ACD")
        saved = self.sql(f"select public.pf_save_project('{self.id}','Renamed','ACD','{self.snapshot}','{self.version}');", self.owner)
        document = json.loads(saved.stdout)
        self.assertNotEqual(document["updatedAt"], self.version)
        self.assertEqual(document["name"], "Renamed")
        self.assertEqual(self.sql(f"select count(*) from public.analyses where project_id='{self.id}';", self.owner).stdout.strip(), "1")
        self.sql(f"select public.pf_delete_project('{self.id}','{document['updatedAt']}');", self.owner)
        self.assertEqual(self.sql(f"select count(*) from public.analyses where project_id='{self.id}';").stdout.strip(), "0")

    def test_foreign_user_cannot_read_write_or_delete_owner_resources(self):
        for table, predicate in [("projects", f"id='{self.id}'"), ("analyses", f"project_id='{self.id}'"), ("profiles", f"id='{self.owner}'")]:
            self.assertEqual(self.sql(f"select count(*) from public.{table} where {predicate};", self.other).stdout.strip(), "0")
        self.assertEqual(self.sql(f"select public.pf_project_document('{self.id}') is null;", self.other).stdout.strip(), "t")
        for operation in [f"select public.pf_save_project('{self.id}','Stolen','AAA','{self.snapshot}','{self.version}');", f"select public.pf_delete_project('{self.id}','{self.version}');"]:
            denied = self.sql(operation, self.other, check=False)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("Project not found", denied.stderr)
        self.sql(f"update public.projects set name='Stolen' where id='{self.id}'; delete from public.projects where id='{self.id}';", self.other)
        opened = json.loads(self.sql(f"select public.pf_project_document('{self.id}');", self.owner).stdout)
        self.assertEqual(opened["name"], "Study")

    def test_rls_rejects_forged_owner_child_insert_and_ownership_transfer(self):
        operations = [
            f"insert into public.projects(owner_id,name) values ('{self.owner}','Forged');",
            f"insert into public.analyses(project_id,type) values ('{self.id}','descriptor');",
            f"insert into public.alignments(project_id,reference,matrix,gap_penalties) values ('{self.id}','ACD','BLOSUM62','{{}}');",
        ]
        for operation in operations:
            result = self.sql(operation, self.other, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("row-level security", result.stderr)
        transfer = self.sql(f"update public.projects set owner_id='{self.other}' where id='{self.id}';", self.owner, check=False)
        self.assertIn("row-level security", transfer.stderr)

    def test_anon_cannot_access_tables_or_rpc_and_cache_is_private(self):
        for operation in ["select * from public.projects;", f"select public.pf_project_document('{self.id}');", "select * from public.external_cache;"]:
            self.assertNotEqual(self.sql(operation, role="anon", check=False).returncode, 0)
        self.assertNotEqual(self.sql("select * from public.external_cache;", self.owner, check=False).returncode, 0)

    def test_stale_delete_and_two_simultaneous_updates_have_one_winner(self):
        def save(name):
            return self.sql(f"select public.pf_save_project('{self.id}','{name}','ACD','{self.snapshot}','{self.version}');", self.owner, check=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ["First", "Second"]))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        self.assertIn("Project changed", next(result.stderr for result in results if result.returncode != 0))
        deletion = self.sql(f"select public.pf_delete_project('{self.id}','{self.version}');", self.owner, check=False)
        self.assertIn("Project changed", deletion.stderr)

    def test_failed_snapshot_write_rolls_back_project_update(self):
        result = self.sql(f"begin; update public.projects set name='Must roll back' where id='{self.id}'; insert into public.analyses(project_id,type) values ('{self.id}','workspace-v1'); commit;", self.owner, check=False)
        self.assertNotEqual(result.returncode, 0)
        document = json.loads(self.sql(f"select public.pf_project_document('{self.id}');", self.owner).stdout)
        self.assertEqual(document["name"], "Study")
        self.assertEqual(document["updatedAt"], self.version)

    def listing(self, query="", order="recent", limit=31, offset=0, user=None):
        statement = f"select coalesce(jsonb_agg(p), '[]'::jsonb) from public.pf_list_projects({limit},{offset},'{query}','{order}',40) p;"
        return json.loads(self.sql(statement, user or self.owner).stdout)

    def test_preview_listing_is_owner_scoped_compact_and_searches_before_pagination(self):
        self.sql(f"select public.pf_save_project(null,'Alpha','{'A' * 1000}','{self.snapshot}',null);", self.owner)
        self.sql(f"select public.pf_save_project(null,'alpha 100%_','ACD','{self.snapshot}',null);", self.owner)
        self.assertEqual(self.listing(user=self.other), [])
        rows = self.listing(query="ALPHA", order="name", limit=1)
        self.assertEqual(rows[0]["name"], "Alpha")
        self.assertEqual(rows[0]["preview"]["sequenceLength"], 1000)
        self.assertEqual(rows[0]["preview"]["sequencePrefix"], "A" * 40)
        self.assertNotIn("active_sequence", rows[0])
        self.assertNotIn("workspace", rows[0])
        self.assertEqual(self.listing(query="ALPHA", order="name", limit=1, offset=1)[0]["name"], "alpha 100%_")
        self.assertEqual(len(self.listing(query="%_")), 1)
        self.assertEqual(self.listing(query="missing"), [])
        self.assertNotEqual(self.sql("select * from public.pf_list_projects(1,0,'','recent',40);", role="anon", check=False).returncode, 0)

    def test_preview_projection_omits_stale_evidence_and_preserves_real_counts(self):
        result = {"massDa": 307.3, "isoelectricPoint": 3.5, "instabilityIndex": 2, "aliphaticIndex": 3, "gravy": 0, "length": 3}
        snapshot = json.loads(self.snapshot)
        snapshot["source"] = {"origin": "catalog", "accession": "P01308", "sequence": "ACD"}
        snapshot["descriptors"] = {"sequence": "ACD", "excluded": 0, "methodVersion": "proteinforge-descriptors-v1", "result": result}
        snapshot["alignment"]["result"] = {"alignedFirst": "ACD", "alignedSecond": "ACD", "marks": "|||", "identities": 3, "similarities": 3, "columns": 3, "score": 10, "parameters": {"matrix": "BLOSUM62", "mode": "global", "gapOpen": 10, "gapExtend": 0.5, "terminalGaps": "free"}}
        payload = json.dumps(snapshot)
        self.sql(f"select public.pf_save_project('{self.id}','Study','ACD','{payload}','{self.version}');", self.owner)
        preview = self.listing()[0]["preview"]
        self.assertEqual(preview["descriptors"], result)
        self.assertEqual(preview["source"], {"origin": "catalog", "accession": "P01308"})
        self.assertEqual(preview["alignment"]["result"], {"identities": 3, "similarities": 3, "columns": 3})
        self.sql(f"update public.projects set active_sequence='AAA' where id='{self.id}'; update public.analyses set results=jsonb_set(results,'{{alignment,first}}','\"AAA\"') where project_id='{self.id}';", self.owner)
        preview = self.listing()[0]["preview"]
        self.assertIsNone(preview["descriptors"])
        self.assertIsNone(preview["source"])
        self.assertIsNone(preview["alignment"]["result"])

    def test_preview_migration_is_idempotent_and_query_bounds_are_enforced(self):
        migration = Path(__file__).resolve().parents[1] / "supabase/migrations/20261019000001_project_previews.sql"
        self.sql(migration.read_text(encoding="utf-8"))
        self.assertEqual(self.listing()[0]["id"], self.id)
        for args in ["0,0,'','recent',40", "1,0,'','unknown',40", "1,0,'','recent',41", "1,-1,'','recent',40"]:
            self.assertNotEqual(self.sql(f"select * from public.pf_list_projects({args});", self.owner, check=False).returncode, 0)
