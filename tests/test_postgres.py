"""Execute migrations, policies and concurrent writes in actual disposable PostgreSQL.

Use scripts/test-postgres.ps1. The bootstrap emulates auth.uid(), not Supabase Auth/PostgREST.
"""
import concurrent.futures
import json
import os
import subprocess
import unittest
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
