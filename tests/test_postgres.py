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
MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase/migrations"


@unittest.skipUnless(DATABASE, "Disposable local PostgreSQL not configured; run scripts/test-postgres.ps1")
class PostgresSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlparse(DATABASE)
        if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/pf_security_test":
            raise RuntimeError("Refusing to run database checks outside disposable local pf_security_test")

    def sql(self, sql, user=None, role="authenticated", check=True, stdin=False):
        prefix = f"set role {role}; set request.jwt.claim.sub = '{user or ''}';" if user is not None or role == "anon" else ""
        # Large snapshots go through standard input: Windows limits a command line to 32,767 characters.
        arguments, script = ([], prefix + sql) if stdin else (["-c", prefix + sql], None)
        process = subprocess.run([PSQL, DATABASE, "-X", "-v", "ON_ERROR_STOP=1", "-Atq", *arguments], input=script, capture_output=True, text=True, encoding="utf-8")
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
        for operation in operations[:2]:
            result = self.sql(operation, self.other, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("row-level security", result.stderr)
        # Nothing writes alignments any more (20261102000001); the privilege is gone before RLS applies.
        self.assertIn("permission denied", self.sql(operations[2], self.other, check=False).stderr)
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

    def keyed_create(self, key, name="Keyed", user=None, check=True):
        return self.sql(f"select public.pf_save_project(null,'{name}','ACD','{self.snapshot}',null,'{key}');", user or self.owner, check=check)

    def test_create_request_id_retry_returns_the_same_project_per_owner(self):
        key = str(uuid4())
        first = json.loads(self.keyed_create(key).stdout)
        # A retry after a lost response, even with other content, returns the stored project untouched.
        retry = json.loads(self.keyed_create(key, name="Retried").stdout)
        self.assertEqual((retry["id"], retry["name"], retry["updatedAt"]), (first["id"], "Keyed", first["updatedAt"]))
        self.assertEqual(self.sql(f"select count(*) from public.projects where create_request_id='{key}';").stdout.strip(), "1")
        foreign = json.loads(self.keyed_create(key, user=self.other).stdout)
        self.assertNotEqual(foreign["id"], first["id"])
        self.assertEqual(self.sql(f"select public.pf_project_document('{first['id']}') is null;", self.other).stdout.strip(), "t")
        unkeyed = [json.loads(self.sql(f"select public.pf_save_project(null,'Plain','ACD','{self.snapshot}',null,null);", self.owner).stdout)["id"] for _ in range(2)]
        self.assertNotEqual(unkeyed[0], unkeyed[1])
        concurrent_key = str(uuid4())
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda name: self.keyed_create(concurrent_key, name=name, check=False), ["First", "Second"]))
        self.assertEqual([result.returncode for result in results], [0, 0], [result.stderr for result in results])
        self.assertEqual(len({json.loads(result.stdout)["id"] for result in results}), 1)
        # Deleting the created project frees the key; updates never take one.
        self.sql(f"select public.pf_delete_project('{first['id']}','{first['updatedAt']}');", self.owner)
        self.assertNotEqual(json.loads(self.keyed_create(key).stdout)["id"], first["id"])
        updated = self.sql(f"select public.pf_save_project('{self.id}','Study','ACD','{self.snapshot}','{self.version}','{key}');", self.owner, check=False)
        self.assertIn("Invalid workspace", updated.stderr)
        anonymous = self.sql(f"select public.pf_save_project(null,'Anon','ACD','{self.snapshot}',null,'{uuid4()}');", role="anon", check=False)
        self.assertNotEqual(anonymous.returncode, 0)

    def test_update_conflicts_are_unchanged_by_create_idempotency(self):
        self.reapply("20261102000000_project_create_idempotency.sql", times=2)
        saved = json.loads(self.sql(f"select public.pf_save_project('{self.id}','Renamed','ACD','{self.snapshot}','{self.version}');", self.owner).stdout)
        stale = self.sql(f"select public.pf_save_project('{self.id}','Lost','ACD','{self.snapshot}','{self.version}');", self.owner, check=False)
        self.assertIn("Project changed", stale.stderr)
        self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{self.id}');", self.owner).stdout)["name"], saved["name"])
        overloads = self.sql("select count(*) from pg_proc where proname='pf_save_project' and pronamespace='public'::regnamespace;").stdout.strip()
        self.assertEqual(overloads, "1")

    def test_direct_table_writes_meet_the_api_bounds(self):
        project = f"insert into public.projects(owner_id,name,active_sequence,status) values ('{self.owner}',%s,%s,%s);"
        workspace = f"update public.analyses set results=%s, parameters=%s where project_id='{self.id}';"
        rejected = {
            "projects_name": [project % ("''", "'ACD'", "'draft'"), project % ("'   '", "'ACD'", "'draft'"),
                              project % (f"'{'n' * 121}'", "'ACD'", "'draft'"), project % ("E'Tab\\tname'", "'ACD'", "'draft'")],
            "projects_active_sequence": [project % ("'Direct'", "'ACDJ'", "'draft'"), project % ("'Direct'", "repeat('A',100001)", "'draft'")],
            "projects_status": [project % ("'Direct'", "'ACD'", "'published'")],
            # PostgreSQL checks constraints by name and reports the first failure, so each case breaks only one.
            "analyses_type": [f"""insert into public.analyses(project_id,type,results,parameters) values ('{self.id}','descriptor','{self.snapshot}','{{"schemaVersion":1}}');"""],
            "analyses_workspace": [
                workspace % ("'{}'", """'{"schemaVersion":null}'"""),
                workspace % ("""'{"schemaVersion":5}'""", """'{"schemaVersion":5}'"""),
                workspace % ("""'{"schemaVersion":"1"}'""", """'{"schemaVersion":"1"}'"""),
                workspace % ("jsonb_build_object('schemaVersion',1,'pad',repeat('x',1900000))", """'{"schemaVersion":1}'"""),
            ],
            "analyses_parameters": [workspace % (f"'{self.snapshot}'", """'{"schemaVersion":1,"pad":"x"}'""")],
        }
        for constraint, operations in rejected.items():
            for operation in operations:
                with self.subTest(constraint=constraint, operation=operation[:90]):
                    result = self.sql(operation, self.owner, check=False, stdin=True)
                    self.assertIn(f'violates check constraint "{constraint}"', result.stderr)
        # Values inside the bounds still go through, directly and through pf_save_project.
        self.sql(project % ("'Direct'", "'ACDUOBZX'", "'draft'"), self.owner)
        self.sql(workspace % (f"'{self.snapshot}'", """'{"schemaVersion":1}'"""), self.owner)
        saved = json.loads(self.sql(f"select public.pf_save_project('{self.id}','{'n' * 120}','{'A' * 100000}','{self.snapshot}','{self.version}');", self.owner, stdin=True).stdout)
        self.assertEqual(len(saved["name"]), 120)
        self.assertEqual(self.sql(f"select count(*) from public.alignments where project_id='{self.id}';", self.owner).stdout.strip(), "0")

    def test_table_constraints_are_not_valid_idempotent_and_spare_stored_rows(self):
        migration = (MIGRATIONS / "20261102000001_table_write_constraints.sql").read_text(encoding="utf-8")
        # A legacy row written before the constraints existed survives a fresh application.
        self.sql("alter table public.projects drop constraint projects_status;")
        self.sql(f"insert into public.projects(owner_id,name,status) values ('{self.owner}','Legacy','archived');")
        for _ in range(2):
            self.sql(migration)
        constraints = self.sql("select string_agg(conname||':'||convalidated, ',' order by conname) from pg_constraint "
                               "where conrelid in ('public.projects'::regclass,'public.analyses'::regclass) and contype='c';").stdout.strip()
        self.assertEqual(constraints, "analyses_parameters:false,analyses_type:false,analyses_workspace:false,"
                                      "projects_active_sequence:false,projects_name:false,projects_status:false")
        self.assertEqual(self.sql(f"select status from public.projects where owner_id='{self.owner}' and name='Legacy';").stdout.strip(), "archived")
        changed = self.sql(f"update public.projects set name='Still legacy' where owner_id='{self.owner}' and name='Legacy';", self.owner, check=False)
        self.assertIn('violates check constraint "projects_status"', changed.stderr)
        self.sql(f"delete from public.projects where owner_id='{self.owner}' and name='Legacy';")

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

    def reapply(self, name, times=1):
        """Reapply an older migration, then every later one, so the newest functions stay in force."""
        migration = MIGRATIONS / name
        for _ in range(times):
            self.sql(migration.read_text(encoding="utf-8"))
        for later in sorted(MIGRATIONS.glob("*.sql")):
            if later.name > name:
                self.sql(later.read_text(encoding="utf-8"))

    def test_preview_migration_is_idempotent_and_query_bounds_are_enforced(self):
        self.reapply("20261019000001_project_previews.sql")
        self.assertEqual(self.listing()[0]["id"], self.id)
        for args in ["0,0,'','recent',40", "1,0,'','unknown',40", "1,0,'','recent',41", "1,-1,'','recent',40"]:
            self.assertNotEqual(self.sql(f"select * from public.pf_list_projects({args});", self.owner, check=False).returncode, 0)

    def lab_snapshot(self):
        from test_laboratory import envelope
        workspace=json.loads(self.snapshot)
        workspace.update(schemaVersion=2,laboratory=envelope())
        return json.dumps(workspace)

    def test_laboratory_round_trip_rename_copy_rls_and_compact_listing(self):
        snapshot=self.lab_snapshot()
        saved=json.loads(self.sql(f"select public.pf_save_project('{self.id}','Laboratory','ACD','{snapshot}','{self.version}');",self.owner).stdout)
        opened=json.loads(self.sql(f"select public.pf_project_document('{self.id}');",self.owner).stdout)
        self.assertEqual(opened['workspace']['laboratory'],json.loads(snapshot)['laboratory'])
        preview=self.listing()[0]['preview']
        self.assertTrue(preview['hasLaboratory']);self.assertEqual(preview['laboratoryVariants'],1)
        self.assertNotIn('documentJson',json.dumps(self.listing()))
        self.assertEqual(self.listing(user=self.other),[])
        self.assertEqual(self.sql(f"select public.pf_project_document('{self.id}') is null;",self.other).stdout.strip(),'t')
        denied=self.sql(f"select public.pf_save_project('{self.id}','Stolen','ACD','{snapshot}','{saved['updatedAt']}');",self.other,check=False)
        self.assertIn('Project not found',denied.stderr)
        renamed=json.loads(self.sql(f"select public.pf_save_project('{self.id}','Renamed','ACD','{snapshot}','{saved['updatedAt']}');",self.owner).stdout)
        copied=json.loads(self.sql(f"select public.pf_save_project(null,'Copy','ACD','{snapshot}',null);",self.owner).stdout)
        self.assertNotEqual(copied['id'],self.id)
        self.sql(f"select public.pf_delete_project('{self.id}','{renamed['updatedAt']}');",self.owner)
        copy=json.loads(self.sql(f"select public.pf_project_document('{copied['id']}');",self.owner).stdout)
        self.assertEqual(copy['workspace'],json.loads(snapshot))

    def test_laboratory_rejects_old_clients_without_partial_name_or_timestamp_write(self):
        snapshot=self.lab_snapshot()
        saved=json.loads(self.sql(f"select public.pf_save_project('{self.id}','Laboratory','ACD','{snapshot}','{self.version}');",self.owner).stdout)
        denied=self.sql(f"select public.pf_save_project('{self.id}','Lost','AAA','{self.snapshot}','{saved['updatedAt']}');",self.owner,check=False)
        self.assertIn('Project requires current client',denied.stderr)
        unchanged=json.loads(self.sql(f"select public.pf_project_document('{self.id}');",self.owner).stdout)
        self.assertEqual(unchanged,saved)
        stale=self.sql(f"select public.pf_save_project('{self.id}','Lost','ACD','{snapshot}','{self.version}');",self.owner,check=False)
        self.assertIn('Project changed',stale.stderr)

    def test_laboratory_sql_validation_size_hash_and_migration_idempotence(self):
        self.reapply('20261021000000_laboratory_projects.sql',times=2)
        snapshot=json.loads(self.lab_snapshot());snapshot['laboratory']['documentSha256']='0'*64
        denied=self.sql(f"select public.pf_save_project('{self.id}','Invalid','ACD','{json.dumps(snapshot)}','{self.version}');",self.owner,check=False)
        self.assertIn('Invalid laboratory',denied.stderr)
        # Construct over-limit text inside PostgreSQL, keeping the shell argument bounded.
        too_large=self.sql("select public.pf_save_project(null,'Too large','ACD',jsonb_build_object('schemaVersion',2,'laboratory',jsonb_build_object('schemaVersion',1,'documentJson',repeat(' ',1000001),'documentSha256','bad')),null);",self.owner,check=False)
        self.assertIn('Invalid laboratory',too_large.stderr)
        self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{self.id}');",self.owner).stdout),self.project)

    def history_snapshot(self, version=3, laboratory=False, history=None):
        from test_generator_history import envelope
        from test_laboratory import envelope as laboratory_envelope
        workspace=json.loads(self.snapshot)
        workspace.update(schemaVersion=version, generatorHistory=envelope() if history is None else history)
        if laboratory:
            workspace['laboratory']=laboratory_envelope()
        return json.dumps(workspace)

    def save(self, snapshot, version=None, name='Study', user=None, check=True):
        """Update this project with a known version, or create one when version is False."""
        target, expected = ("null", "null") if version is False else (f"'{self.id}'", f"'{version or self.version}'")
        # Dollar quoting keeps exported JSON text intact; the payload contains no "$pf$".
        return self.sql(f"select public.pf_save_project({target},'{name}','ACD',$pf${snapshot}$pf$::jsonb,{expected});", user or self.owner, check=check, stdin=True)

    def test_generator_history_round_trip_rls_copy_and_compact_listing(self):
        snapshot=self.history_snapshot(laboratory=True)
        saved=json.loads(self.save(snapshot).stdout)
        opened=json.loads(self.sql(f"select public.pf_project_document('{self.id}');",self.owner).stdout)
        self.assertEqual(opened['workspace'],json.loads(snapshot))
        preview=self.listing()[0]['preview']
        self.assertEqual((preview['generatorRuns'],preview['hasLaboratory'],preview['laboratoryVariants']),(1,True,1))
        self.assertNotIn('resultJson',json.dumps(self.listing()))
        self.assertEqual(self.listing(user=self.other),[])
        self.assertEqual(self.sql(f"select public.pf_project_document('{self.id}') is null;",self.other).stdout.strip(),'t')
        self.assertIn('Project not found',self.save(snapshot,saved['updatedAt'],'Stolen',user=self.other,check=False).stderr)
        copied=json.loads(self.save(snapshot,False,'Copy').stdout)
        self.sql(f"select public.pf_delete_project('{self.id}','{saved['updatedAt']}');",self.owner)
        self.assertEqual(self.sql(f"select count(*) from public.analyses where project_id='{self.id}';").stdout.strip(),'0')
        self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{copied['id']}');",self.owner).stdout)['workspace'],json.loads(snapshot))

    def test_generator_history_versions_are_monotonic_without_partial_writes(self):
        laboratory=json.loads(self.history_snapshot(laboratory=True)); laboratory.pop('generatorHistory'); laboratory['schemaVersion']=2
        upgraded=json.loads(self.save(json.dumps(laboratory)).stdout)
        current=json.loads(self.save(self.history_snapshot(laboratory=True),upgraded['updatedAt']).stdout)
        for older in [json.dumps(laboratory),self.snapshot]:
            denied=self.save(older,current['updatedAt'],'Lost',check=False)
            self.assertIn('Project requires current client',denied.stderr)
            self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{self.id}');",self.owner).stdout),current)
        without=json.loads(self.history_snapshot()); without.pop('generatorHistory')
        kept=json.loads(self.save(json.dumps(without),current['updatedAt'],'Cleared').stdout)
        self.assertEqual(kept['workspace']['schemaVersion'],3)
        self.assertEqual(self.listing()[0]['preview']['generatorRuns'],0)
        self.assertIn('Project changed',self.save(self.history_snapshot(),current['updatedAt'],check=False).stderr)
        fresh=json.loads(self.save(self.history_snapshot(),False,'Created').stdout)
        self.assertEqual(fresh['workspace']['schemaVersion'],3)

    def test_two_simultaneous_history_writes_have_one_winner(self):
        def write(name):
            return self.save(self.history_snapshot(),name=name,check=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(write,['First','Second']))
        self.assertEqual(sum(result.returncode==0 for result in results),1)
        self.assertIn('Project changed',next(result.stderr for result in results if result.returncode!=0))

    def test_generator_history_sql_validation_matches_contract(self):
        from test_generator_history import envelope, history_document, run, variant
        def document_with(change):
            doc=history_document(); change(doc); return envelope(doc)
        def result_without_length():
            result=json.loads(variant(42)); result['parameters'].pop('length'); return json.dumps(result,indent=2)
        cases={
            'bad hash':(3,{**envelope(),'documentSha256':'0'*64}),
            'version 2 history':(2,envelope()),
            'version 1 history':(1,envelope()),
            'wrong run id':(3,document_with(lambda d:d['runs'][0].update(id='0'*64))),
            'eleven runs':(3,envelope(history_document([run(variant(seed)) for seed in range(1,12)]))),
            'missing parameter':(3,envelope(history_document([run(result_without_length())]))),
            'odd selection':(3,document_with(lambda d:d['runs'][0].update(selection={'x':1,'y':2}))),
            'selection outside arm':(3,document_with(lambda d:d['runs'][0].update(selection={'arm':'reference','index':5}))),
            'time without zone':(3,document_with(lambda d:d['runs'][0].update(recordedAt='2026-10-27T09:30:00'))),
            'external origin':(3,document_with(lambda d:d['runs'][0].update(origin='design-service'))),
        }
        for name,(version,history) in cases.items():
            with self.subTest(name):
                denied=self.save(self.history_snapshot(version,laboratory=version==2,history=history),check=False)
                self.assertNotEqual(denied.returncode,0)
                self.assertIn('Invalid',denied.stderr)
        self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{self.id}');",self.owner).stdout),self.project)
        ten=self.history_snapshot(history=envelope(history_document([run(variant(seed)) for seed in range(1,11)])))
        saved=json.loads(self.save(ten).stdout)
        self.assertEqual(saved['workspace']['generatorHistory'],json.loads(ten)['generatorHistory'])
        too_large=self.sql("select public.pf_save_project(null,'Too large','ACD',jsonb_build_object('schemaVersion',3,'generatorHistory',jsonb_build_object('schemaVersion',1,'documentJson',repeat(' ',100001),'documentSha256','bad')),null);",self.owner,check=False)
        self.assertIn('Invalid generator history',too_large.stderr)

    def test_generator_history_migration_is_idempotent_and_private(self):
        self.reapply('20261027000000_generator_history.sql',times=2)
        self.save(self.history_snapshot())
        self.assertEqual(self.listing()[0]['preview']['generatorRuns'],1)
        for function in ["public.pf_validate_generator_history('{}'::jsonb)","public.pf_generator_run_count('{}'::jsonb)"]:
            self.assertNotEqual(self.sql(f"select {function};",role='anon',check=False).returncode,0)

    def prediction_snapshot(self):
        import hashlib
        from app.folding_models import FoldResult, sequence_hash
        from test_folding import pdb_for
        from test_laboratory import envelope, laboratory_fixture
        prediction = FoldResult(sequence='ACD', sequence_sha256=sequence_hash('ACD'), pdb=pdb_for(),
                                pdb_sha256=hashlib.sha256(pdb_for().encode()).hexdigest(), retrieved_at='2026-10-30T00:00:00Z').model_dump(by_alias=True)
        doc = {**laboratory_fixture(), 'schemaVersion': 2, 'predictions': {'a'*64: prediction}}
        workspace = json.loads(self.history_snapshot())
        workspace.update(schemaVersion=4, laboratory=envelope(doc))
        return workspace

    def test_prediction_round_trip_rls_preview_monotonic_guard_and_cascade(self):
        snapshot = self.prediction_snapshot()
        saved = json.loads(self.save(json.dumps(snapshot)).stdout)
        self.assertEqual(saved['workspace'], snapshot)
        self.assertEqual((self.listing()[0]['preview']['generatorRuns'], self.listing()[0]['preview']['laboratoryVariants']), (1, 1))
        self.assertNotIn('pdb', json.dumps(self.listing()))
        self.assertEqual(self.sql(f"select public.pf_project_document('{self.id}') is null;", self.other).stdout.strip(), 't')
        self.assertIn('Project not found', self.save(json.dumps(snapshot), saved['updatedAt'], user=self.other, check=False).stderr)
        denied_delete = self.sql(f"select public.pf_delete_project('{self.id}','{saved['updatedAt']}');", self.other, check=False)
        self.assertIn('Project not found', denied_delete.stderr)
        for older in (self.snapshot, self.lab_snapshot(), self.history_snapshot()):
            self.assertIn('Project requires current client', self.save(older, saved['updatedAt'], 'Lost', check=False).stderr)
        self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{self.id}');", self.owner).stdout), saved)
        self.sql(f"select public.pf_delete_project('{self.id}','{saved['updatedAt']}');", self.owner)
        self.assertEqual(self.sql(f"select count(*) from public.analyses where project_id='{self.id}';").stdout.strip(), '0')

    def test_prediction_validation_idempotence_and_no_partial_write(self):
        import copy
        from test_laboratory import envelope
        self.reapply('20261030000000_structure_predictions.sql', times=2)
        original = self.prediction_snapshot()
        for mutate in (lambda p: p.update(sequence='ACE'), lambda p: p.update(pdbSha256='0'*64),
                       lambda p: p.update(ptm=.7), lambda p: p.pop('sourceUrl'),
                       lambda p: p.update(retrievedAt='2026-10-30T00:00:00')):
            snapshot = copy.deepcopy(original)
            doc = json.loads(snapshot['laboratory']['documentJson']); mutate(doc['predictions']['a'*64])
            snapshot['laboratory'] = envelope(doc)
            denied = self.save(json.dumps(snapshot), check=False)
            self.assertIn('Invalid laboratory', denied.stderr)
        for version in (1, 2, 3):
            snapshot = {**original, 'schemaVersion': version}
            self.assertIn('Invalid laboratory', self.save(json.dumps(snapshot), check=False).stderr)
        self.assertIn('Invalid laboratory', self.save(json.dumps({**original, 'laboratory': None}), check=False).stderr)
        self.assertEqual(json.loads(self.sql(f"select public.pf_project_document('{self.id}');", self.owner).stdout), self.project)
        for function in ("public.pf_validate_prediction('{}'::jsonb,'ACD')", "public.pf_validate_laboratory_v1('{}'::jsonb)"):
            self.assertNotEqual(self.sql(f'select {function};', role='anon', check=False).returncode, 0)
        doc = json.loads(original['laboratory']['documentJson']); doc['predictions'] = {}
        original['laboratory'] = envelope(doc)
        self.assertEqual(json.loads(self.save(json.dumps(original)).stdout)['workspace']['schemaVersion'], 4)

    # Reports: Storage is emulated by inserting/deleting storage.objects rows under the caller's
    # role and claims, as the Storage API does; signed URLs and file bytes are not exercised here.
    SHA = 'a' * 64

    def reserve(self, user=None, project=None, kind='pdf', size=100, check=True):
        target = f"'{project}'" if project else 'null'
        result = self.sql(f"select public.pf_reserve_report({target},'{kind}',{size},'{self.SHA}');", user or self.owner, check=check)
        return json.loads(result.stdout) if check else result

    def upload(self, path, size=100, mime='application/pdf', user=None, check=True):
        metadata = json.dumps({'size': size, 'mimetype': mime})
        statement = f"insert into storage.objects(bucket_id,name,owner,metadata) values ('reports','{path}','{user or self.owner}','{metadata}');"
        return self.sql(statement, user or self.owner, check=check)

    def report_object(self, report_id, user=None):
        return json.loads(self.sql(f"select public.pf_report_object('{report_id}');", user or self.owner).stdout.strip() or 'null')

    def reports(self, user=None, project=None):
        target = f"'{project}'" if project else 'null'
        statement = f"select coalesce(jsonb_agg(r), '[]'::jsonb) from public.pf_list_reports(51,0,{target}) r;"
        return json.loads(self.sql(statement, user or self.owner).stdout)

    def save_content(self, name, sequence, version, user=None):
        result = self.sql(f"select public.pf_save_project('{self.id}','{name}','{sequence}','{self.snapshot}','{version}');", user or self.owner)
        return json.loads(result.stdout)

    def versions(self, user=None):
        return json.loads(self.sql(f"select public.pf_list_project_versions('{self.id}', 40);", user or self.owner).stdout)

    def test_versions_keep_replaced_content_not_renames_and_stop_at_five(self):
        renamed = self.save_content("Renamed", "ACD", self.version)
        self.assertEqual(self.versions(), [])
        current = renamed
        for sequence in ("ACDE", "ACDEF", "ACDEFG", "ACDEFGH", "ACDEFGHI", "ACDEFGHIK"):
            current = self.save_content("Renamed", sequence, current["updatedAt"])
        listed = self.versions()
        # Newest first, five at most: the oldest content (ACD) has been dropped.
        self.assertEqual([item["preview"]["sequencePrefix"] for item in listed], ["ACDEFGHI", "ACDEFGH", "ACDEFG", "ACDEF", "ACDE"])
        self.assertEqual(len({item["id"] for item in listed}), 5)
        self.assertNotIn("workspace", listed[0])
        self.assertEqual(self.sql(f"select count(*) from public.project_versions where project_id='{self.id}';").stdout.strip(), "5")

    def test_versions_are_private_read_only_and_go_with_their_project(self):
        current = self.save_content("Study", "ACDE", self.version)
        self.assertEqual(len(self.versions()), 1)
        self.assertEqual(self.versions(self.other), [])
        self.assertEqual(self.sql(f"select count(*) from public.project_versions where project_id='{self.id}';", self.other).stdout.strip(), "0")
        for operation in (
            f"insert into public.project_versions(project_id,owner_id,active_sequence,workspace,saved_at) values ('{self.id}','{self.owner}','WWW','{self.snapshot}',now());",
            f"update public.project_versions set active_sequence='WWW' where project_id='{self.id}';",
            f"delete from public.project_versions where project_id='{self.id}';",
        ):
            self.assertIn("permission denied", self.sql(operation, self.owner, check=False).stderr)
        # Calling the writer directly only copies what the caller already has; another owner copies nothing.
        self.sql(f"select public.pf_keep_project_version('{self.id}');", self.other)
        self.assertEqual(len(self.versions()), 1)
        self.assertNotEqual(self.sql(f"select public.pf_list_project_versions('{self.id}', 40);", role="anon", check=False).returncode, 0)
        self.sql(f"select public.pf_delete_project('{self.id}','{current['updatedAt']}');", self.owner)
        self.assertEqual(self.sql(f"select count(*) from public.project_versions where project_id='{self.id}';").stdout.strip(), "0")

    def test_restore_brings_content_back_keeps_the_name_and_can_be_undone(self):
        current = self.save_content("Study", "ACDE", self.version)
        current = self.save_content("Renamed", "ACDE", current["updatedAt"])
        kept = self.versions()[0]
        restored = json.loads(self.sql(f"select public.pf_restore_project_version('{self.id}','{kept['id']}','{current['updatedAt']}');", self.owner).stdout)
        self.assertEqual((restored["name"], restored["activeSequence"]), ("Renamed", "ACD"))
        self.assertNotEqual(restored["updatedAt"], current["updatedAt"])
        listed = self.versions()
        # What was current is now the only version; the restored one is not listed twice.
        self.assertEqual([item["preview"]["sequencePrefix"] for item in listed], ["ACDE"])
        undone = json.loads(self.sql(f"select public.pf_restore_project_version('{self.id}','{listed[0]['id']}','{restored['updatedAt']}');", self.owner).stdout)
        self.assertEqual(undone["activeSequence"], "ACDE")

    def test_restore_refuses_stale_foreign_and_mismatched_requests_without_writing(self):
        current = self.save_content("Study", "ACDE", self.version)
        kept = self.versions()[0]
        stale = self.sql(f"select public.pf_restore_project_version('{self.id}','{kept['id']}','{self.version}');", self.owner, check=False)
        self.assertIn("Project changed", stale.stderr)
        foreign = self.sql(f"select public.pf_restore_project_version('{self.id}','{kept['id']}','{current['updatedAt']}');", self.other, check=False)
        self.assertIn("Version not found", foreign.stderr)
        other_project = json.loads(self.sql(f"select public.pf_save_project(null,'Other','WWW','{self.snapshot}',null);", self.owner).stdout)
        mismatched = self.sql(f"select public.pf_restore_project_version('{other_project['id']}','{kept['id']}','{other_project['updatedAt']}');", self.owner, check=False)
        self.assertIn("Version not found", mismatched.stderr)
        opened = json.loads(self.sql(f"select public.pf_project_document('{self.id}');", self.owner).stdout)
        self.assertEqual((opened["activeSequence"], opened["updatedAt"]), ("ACDE", current["updatedAt"]))
        self.assertEqual(len(self.versions()), 1)

    def test_a_link_shows_analysis_and_comparison_to_anyone_holding_its_hash(self):
        digest = "a" * 64
        shared = json.loads(self.sql(f"select public.pf_share_project('{self.id}','{digest}');", self.owner).stdout)
        self.assertIn("expiresAt", shared)
        opened = json.loads(self.sql(f"select public.pf_shared_project('{digest}');", role="anon").stdout)
        self.assertEqual((opened["name"], opened["activeSequence"]), ("Study", "ACD"))
        self.assertEqual(opened["workspace"]["schemaVersion"], 1)
        self.assertEqual(set(opened["workspace"]), {"schemaVersion", "profileWindow", "source", "descriptors", "alignment"})
        self.assertNotIn("owner", json.dumps(opened).lower())
        state = json.loads(self.sql(f"select public.pf_project_share('{self.id}');", self.owner).stdout)
        self.assertNotIn("tokenSha256", state)
        self.assertEqual(self.sql(f"select public.pf_project_share('{self.id}') is null;", self.other).stdout.strip(), "t")

    def test_links_are_replaced_revoked_expired_and_owner_only(self):
        first, second = "b" * 64, "c" * 64
        self.sql(f"select public.pf_share_project('{self.id}','{first}');", self.owner)
        self.sql(f"select public.pf_share_project('{self.id}','{second}');", self.owner)
        self.assertEqual(self.sql(f"select public.pf_shared_project('{first}') is null;", role="anon").stdout.strip(), "t")
        self.assertEqual(self.sql(f"select public.pf_shared_project('{second}') is null;", role="anon").stdout.strip(), "f")
        self.assertIn("Project not found", self.sql(f"select public.pf_share_project('{self.id}','{'d' * 64}');", self.other, check=False).stderr)
        self.sql(f"select public.pf_unshare_project('{self.id}');", self.other)
        self.assertEqual(self.sql(f"select public.pf_shared_project('{second}') is null;", role="anon").stdout.strip(), "f")
        self.sql(f"update public.project_shares set expires_at = now() - interval '1 second' where project_id='{self.id}';")
        self.assertEqual(self.sql(f"select public.pf_shared_project('{second}') is null;", role="anon").stdout.strip(), "t")
        self.assertEqual(self.sql(f"select public.pf_project_share('{self.id}') is null;", self.owner).stdout.strip(), "t")
        self.sql(f"select public.pf_share_project('{self.id}','{first}');", self.owner)
        self.sql(f"select public.pf_unshare_project('{self.id}');", self.owner)
        self.assertEqual(self.sql(f"select public.pf_shared_project('{first}') is null;", role="anon").stdout.strip(), "t")

    def test_link_table_is_private_and_written_only_by_the_server(self):
        digest = "e" * 64
        self.sql(f"select public.pf_share_project('{self.id}','{digest}');", self.owner)
        self.assertEqual(self.sql("select count(*) from public.project_shares;", self.other).stdout.strip(), "0")
        self.assertNotEqual(self.sql("select count(*) from public.project_shares;", role="anon", check=False).returncode, 0)
        for operation in (
            f"insert into public.project_shares(project_id,owner_id,token_sha256,expires_at) values ('{self.id}','{self.owner}','{'f' * 64}',now());",
            f"update public.project_shares set expires_at = now() + interval '100 years' where project_id='{self.id}';",
            f"delete from public.project_shares where project_id='{self.id}';",
        ):
            self.assertIn("permission denied", self.sql(operation, self.owner, check=False).stderr)
        for call in (f"select public.pf_share_project('{self.id}','{digest}');", f"select public.pf_project_share('{self.id}');", f"select public.pf_unshare_project('{self.id}');"):
            self.assertNotEqual(self.sql(call, role="anon", check=False).returncode, 0)
        self.assertIn("Invalid token", self.sql(f"select public.pf_share_project('{self.id}','not-a-hash');", self.owner, check=False).stderr)
        self.sql(f"select public.pf_delete_project('{self.id}','{self.version}');", self.owner)
        self.assertEqual(self.sql(f"select public.pf_shared_project('{digest}') is null;", role="anon").stdout.strip(), "t")
        self.assertEqual(self.sql("select count(*) from public.project_shares where token_sha256 = '" + digest + "';").stdout.strip(), "0")

    def test_report_reserve_upload_complete_list_and_delete_are_owner_scoped(self):
        reserved = self.reserve(project=self.id)
        report, path = reserved['report'], reserved['objectPath']
        self.assertEqual((report['status'], report['projectId'], report['completedAt']), ('pending', self.id, None))
        self.assertEqual(path, f"{self.owner}/{report['id']}.pdf")
        complete = f"select public.pf_complete_report('{report['id']}');"
        self.assertIn('Report upload missing', self.sql(complete, self.owner, check=False).stderr)
        self.assertIn('row-level security', self.upload(path, user=self.other, check=False).stderr)
        self.assertIsNone(self.report_object(report['id'], self.other))
        self.assertEqual(self.sql("select count(*) from public.reports;", self.other).stdout.strip(), '0')
        for operation in [complete, f"select public.pf_delete_report('{report['id']}');"]:
            self.assertIn('Report not found', self.sql(operation, self.other, check=False).stderr)
        wrong = self.reserve(kind='json')
        self.upload(wrong['objectPath'], size=99, mime='application/json')
        self.assertIn('Report upload missing', self.sql(f"select public.pf_complete_report('{wrong['report']['id']}');", self.owner, check=False).stderr)
        self.upload(path)
        ready = json.loads(self.sql(complete, self.owner).stdout)
        self.assertEqual(ready['status'], 'ready')
        self.assertIsNotNone(ready['completedAt'])
        self.assertEqual(json.loads(self.sql(complete, self.owner).stdout), ready)
        self.assertEqual([row['id'] for row in self.reports()], [wrong['report']['id'], report['id']])
        self.assertEqual([row['id'] for row in self.reports(project=self.id)], [report['id']])
        self.assertNotIn('object_path', json.dumps(self.reports()))
        self.assertEqual(self.reports(self.other), [])
        self.assertEqual(self.sql("select count(*) from storage.objects;", self.other).stdout.strip(), '0')
        self.sql(f"delete from storage.objects where name='{path}';", self.other)
        self.assertEqual(self.sql(f"select count(*) from storage.objects where name='{path}';").stdout.strip(), '1')
        self.sql(f"delete from storage.objects where name='{path}';", self.owner)
        self.sql(f"select public.pf_delete_report('{report['id']}');", self.owner)
        self.assertIsNone(self.report_object(report['id']))
        self.assertIn('Report not found', self.sql(f"select public.pf_delete_report('{report['id']}');", self.owner, check=False).stderr)

    def test_report_server_columns_cannot_be_forged_or_changed(self):
        statement = (f"insert into public.reports(owner_id,kind,size_bytes,sha256,status,object_path,completed_at) "
                     f"values ('{self.other}','json',10,'{self.SHA}','ready','{self.other}/x.json',now()) "
                     "returning owner_id||' '||status||' '||object_path||' '||(completed_at is null);")
        owner, status, path, unfinished = self.sql(statement, self.owner).stdout.strip().split(' ')
        self.assertEqual((owner, status, unfinished), (self.owner, 'pending', 'true'))
        self.assertTrue(path.startswith(f"{self.owner}/") and path.endswith('.json'))
        report = self.reserve(project=self.id)['report']
        second = json.loads(self.sql(f"select public.pf_save_project(null,'Second','ACD','{self.snapshot}',null);", self.owner).stdout)['id']
        for change in [f"sha256='{'b' * 64}'", "size_bytes=1", f"object_path='{self.owner}/y.pdf'", f"project_id='{second}'", "kind='json'"]:
            with self.subTest(change=change):
                self.assertIn('Invalid report', self.sql(f"update public.reports set {change} where id='{report['id']}';", self.owner, check=False).stderr)
        self.assertIn('Report upload missing', self.sql(f"update public.reports set status='ready' where id='{report['id']}';", self.owner, check=False).stderr)
        foreign = json.loads(self.sql(f"select public.pf_save_project(null,'Foreign','ACD','{self.snapshot}',null);", self.other).stdout)['id']
        self.assertIn('Project not found', self.reserve(project=foreign, check=False).stderr)
        direct = f"insert into public.reports(project_id,kind,size_bytes,sha256) values ('{foreign}','pdf',1,'{self.SHA}');"
        self.assertIn('Project not found', self.sql(direct, self.owner, check=False).stderr)
        self.sql(f"select public.pf_delete_project('{self.id}','{self.version}');", self.owner)
        self.assertIsNone(self.report_object(report['id'])['report']['projectId'])
        for operation in ["select * from public.reports;", f"select public.pf_reserve_report(null,'pdf',1,'{self.SHA}');", "select * from public.pf_list_reports(1,0,null);"]:
            self.assertNotEqual(self.sql(operation, role='anon', check=False).returncode, 0)

    def test_report_bounds_quota_concurrency_and_reservation_expiry(self):
        for kind, size, sha in [('docx', 1, self.SHA), ('pdf', 0, self.SHA), ('pdf', 5000001, self.SHA), ('pdf', 1, 'A' * 64), ('pdf', 1, 'a' * 63)]:
            with self.subTest(kind=kind, size=size, sha=sha[:2]):
                result = self.sql(f"select public.pf_reserve_report(null,'{kind}',{size},'{sha}');", self.owner, check=False)
                self.assertIn('Invalid report', result.stderr)
        self.sql(f"select public.pf_reserve_report(null,'pdf',1,'{self.SHA}') from generate_series(1,19);", self.owner)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.reserve(size=1, check=False), range(2)))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        self.assertIn('Report quota exceeded', next(result.stderr for result in results if result.returncode != 0))
        self.sql(f"select public.pf_reserve_report(null,'pdf',5000000,'{self.SHA}') from generate_series(1,5);", self.other)
        self.assertIn('Report quota exceeded', self.reserve(self.other, size=1, check=False).stderr)
        kept = self.reports()[0]
        self.upload(f"{self.owner}/{kept['id']}.pdf", size=1)
        # Age the reservations past the two-hour upload window without firing the immutability trigger.
        self.sql(f"set session_replication_role = replica; update public.reports set created_at = created_at - interval '3 hours' where owner_id = '{self.owner}';")
        late = self.reports()[1]
        self.assertIn('row-level security', self.upload(f"{self.owner}/{late['id']}.pdf", size=1, check=False).stderr)
        fresh = self.reserve(size=1)['report']
        self.assertEqual({row['id'] for row in self.reports()}, {kept['id'], fresh['id']})
        self.assertEqual(json.loads(self.sql(f"select public.pf_complete_report('{kept['id']}');", self.owner).stdout)['status'], 'ready')

    def test_report_migration_is_idempotent_and_bucket_is_private(self):
        self.reapply('20261101000000_report_storage.sql', times=2)
        bucket = self.sql("select public||' '||file_size_limit||' '||array_to_string(allowed_mime_types,',') from storage.buckets where id='reports';").stdout.strip()
        self.assertEqual(bucket, 'false 5000000 application/pdf,application/json,text/csv')
        self.assertEqual(self.reserve()['report']['status'], 'pending')
        for function in ["public.pf_report_object(gen_random_uuid())", "public.pf_complete_report(gen_random_uuid())", "public.pf_delete_report(gen_random_uuid())"]:
            self.assertNotEqual(self.sql(f"select {function};", role='anon', check=False).returncode, 0)

    def create_projects(self, count, user=None):
        # Rows straight away in one session; each create still runs pf_save_project and its insert trigger.
        self.sql(f"select count(public.pf_save_project(null,'Bulk '||g,'ACD','{self.snapshot}',null)) from generate_series(1,{count}) g;", user or self.owner)

    def project_count(self, user):
        return int(self.sql(f"select count(*) from public.projects where owner_id='{user}';").stdout.strip())

    def test_project_quota_refuses_the_fifty_first_create_but_never_an_update(self):
        key = str(uuid4())
        self.keyed_create(key)
        self.create_projects(48)
        self.assertEqual(self.project_count(self.owner), 50)
        refused = self.sql(f"select public.pf_save_project(null,'Extra','ACD','{self.snapshot}',null);", self.owner, check=False)
        self.assertIn("Project quota exceeded", refused.stderr)
        self.assertEqual(self.project_count(self.owner), 50)
        # A retried create with a stored request id is the same project, not a new one.
        self.assertEqual(self.keyed_create(key).returncode, 0)
        self.assertIn("Project quota exceeded", self.keyed_create(str(uuid4()), check=False).stderr)
        # A direct insert under RLS meets the same quota.
        direct = self.sql(f"insert into public.projects(owner_id,name,active_sequence) values ('{self.owner}','Direct','ACD');", self.owner, check=False)
        self.assertIn("Project quota exceeded", direct.stderr)
        saved = json.loads(self.sql(f"select public.pf_save_project('{self.id}','Still editable','ACDE','{self.snapshot}','{self.version}');", self.owner).stdout)
        self.assertEqual(saved["name"], "Still editable")
        # The quota is per account, and deleting a project frees a place.
        self.assertEqual(self.keyed_create(str(uuid4()), user=self.other).returncode, 0)
        self.sql(f"select public.pf_delete_project('{self.id}','{saved['updatedAt']}');", self.owner)
        self.assertEqual(self.keyed_create(str(uuid4())).returncode, 0)

    def test_accounts_above_the_quota_keep_and_edit_their_projects(self):
        # Rows from before the quota: replica mode skips the insert trigger, as if they predated it.
        self.sql(f"set session_replication_role = replica; insert into public.projects(owner_id,name,active_sequence) select '{self.owner}','Old '||g,'ACD' from generate_series(1,54) g;")
        self.assertEqual(self.project_count(self.owner), 55)
        self.assertIn("Project quota exceeded", self.keyed_create(str(uuid4()), check=False).stderr)
        saved = json.loads(self.sql(f"select public.pf_save_project('{self.id}','Kept','ACDE','{self.snapshot}','{self.version}');", self.owner).stdout)
        self.assertEqual((saved["name"], saved["activeSequence"]), ("Kept", "ACDE"))
        self.sql(f"select public.pf_delete_project('{self.id}','{saved['updatedAt']}');", self.owner)
        self.assertEqual(self.project_count(self.owner), 54)
        self.assertIn("Project quota exceeded", self.keyed_create(str(uuid4()), check=False).stderr)

    def test_two_simultaneous_creates_cannot_both_take_the_last_place(self):
        self.create_projects(48)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda name: self.keyed_create(str(uuid4()), name=name, check=False), ["First", "Second"]))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1, [result.stderr for result in results])
        self.assertIn("Project quota exceeded", next(result.stderr for result in results if result.returncode != 0))
        self.assertEqual(self.project_count(self.owner), 50)
        # Two creates with the same request id at the limit: both answer with the one stored project.
        self.sql(f"select public.pf_delete_project(id, updated_at) from public.projects where owner_id='{self.owner}' and name in ('First','Second');", self.owner)
        key = str(uuid4())
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            same = list(executor.map(lambda name: self.keyed_create(key, name=name, check=False), ["First", "Second"]))
        self.assertEqual([result.returncode for result in same], [0, 0], [result.stderr for result in same])
        self.assertEqual(len({json.loads(result.stdout)["id"] for result in same}), 1)

    def test_project_quota_migration_is_idempotent_and_its_trigger_is_not_callable(self):
        self.reapply("20261113000000_project_quota.sql", times=2)
        triggers = self.sql("select count(*) from pg_trigger where tgrelid='public.projects'::regclass and tgname='pf_project_quota';").stdout.strip()
        self.assertEqual(triggers, "1")
        callable_by = self.sql("select has_function_privilege('authenticated', 'public.pf_project_before_insert_quota()', 'execute') or has_function_privilege('anon', 'public.pf_project_before_insert_quota()', 'execute');").stdout.strip()
        self.assertEqual(callable_by, "f")
        self.create_projects(49)
        self.assertIn("Project quota exceeded", self.keyed_create(str(uuid4()), check=False).stderr)

    def spend(self, user=None, scope="assistant", limit=3, window=60, global_limit=0, global_window=86400, check=True):
        result = self.sql(f"select public.pf_spend('{scope}',{limit},{window},{global_limit},{global_window});", user or self.owner, check=check)
        return json.loads(result.stdout) if check else result

    def test_spend_counts_one_window_per_account_and_scope(self):
        self.assertEqual([self.spend() for _ in range(4)], [
            {"allowed": True, "remaining": 2}, {"allowed": True, "remaining": 1}, {"allowed": True, "remaining": 0}, {"allowed": False, "remaining": 0},
        ])
        self.assertEqual(self.spend(self.other), {"allowed": True, "remaining": 2})
        self.assertEqual(self.spend(scope="uniprot"), {"allowed": True, "remaining": 2})
        self.assertEqual(self.sql("select public.pf_spend_remaining('assistant',3,60);", self.owner).stdout.strip(), "0")
        self.assertEqual(self.sql("select public.pf_spend_remaining('folding',1,60);", self.owner).stdout.strip(), "1")
        # A window ends p_window_seconds after its first request; the next request opens a new one.
        self.sql(f"update public.spend_windows set started_at = started_at - interval '61 seconds' where subject = '{self.owner}' and scope = 'assistant';")
        self.assertEqual(self.sql("select public.pf_spend_remaining('assistant',3,60);", self.owner).stdout.strip(), "3")
        self.assertEqual(self.spend(), {"allowed": True, "remaining": 2})
        # The scope ceiling applies whatever limit the caller sends, and to a limit of 0 too.
        self.assertEqual(self.spend(scope="folding", limit=50)["remaining"], 2)
        self.assertEqual(self.spend(scope="folding", limit=0)["remaining"], 1)

    def test_simultaneous_spends_never_exceed_the_limit(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.spend(limit=3), range(8)))
        self.assertEqual(sum(result["allowed"] for result in results), 3)
        self.assertEqual(sorted(result["remaining"] for result in results if result["allowed"]), [0, 1, 2])
        self.assertEqual(self.sql(f"select used from public.spend_windows where subject = '{self.owner}' and scope = 'assistant';").stdout.strip(), "3")

    def test_the_shared_window_caps_everyone_and_gives_back_refused_charges(self):
        everyone = "00000000-0000-0000-0000-000000000000"
        self.sql(f"delete from public.spend_windows where subject = '{everyone}' and scope = 'uniprot';")
        users = [str(uuid4()) for _ in range(6)]
        self.sql("insert into auth.users(id) values " + ",".join(f"('{user}')" for user in users) + ";")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda user: self.spend(user, scope="uniprot", limit=5, global_limit=4), users))
        self.assertEqual(sum(result["allowed"] for result in results), 4)
        self.assertEqual(self.sql(f"select used from public.spend_windows where subject = '{everyone}' and scope = 'uniprot';").stdout.strip(), "4")
        # Refused by the shared window: the caller's own window is left as it was, with its full budget.
        refused = [user for user, result in zip(users, results, strict=True) if not result["allowed"]]
        self.assertEqual([result["remaining"] for result in results if not result["allowed"]], [5, 5])
        used = self.sql("select coalesce(sum(used), 0) from public.spend_windows where scope = 'uniprot' and subject in (" + ",".join(f"'{user}'" for user in refused) + ");").stdout.strip()
        self.assertEqual(used, "0")
        self.sql(f"delete from public.spend_windows where subject = '{everyone}' and scope = 'uniprot';")

    def test_spend_is_private_owner_scoped_and_validated(self):
        self.spend()
        for operation in ["select * from public.spend_windows;", "insert into public.spend_windows values ('assistant', gen_random_uuid(), now(), 0);", "update public.spend_windows set used = 0;"]:
            for role in ("authenticated", "anon"):
                with self.subTest(operation=operation, role=role):
                    denied = self.sql(operation, self.owner if role == "authenticated" else None, role=role, check=False)
                    self.assertIn("permission denied", denied.stderr)
        for function in ["public.pf_spend('assistant',3,60,0,86400)", "public.pf_spend_remaining('assistant',3,60)"]:
            self.assertNotEqual(self.sql(f"select {function};", role="anon", check=False).returncode, 0)
        for arguments in ["'other',3,60,0,86400", "'assistant',-1,60,0,86400", "'assistant',3,0,0,86400", "'assistant',3,86401,0,86400",
                          "'assistant',3,60,100001,86400", "'assistant',3,60,0,0", "'assistant',null,60,0,86400"]:
            with self.subTest(arguments=arguments):
                self.assertIn("Invalid spend", self.sql(f"select public.pf_spend({arguments});", self.owner, check=False).stderr)
        self.assertIn("Authentication required", self.sql("select public.pf_spend('assistant',3,60,0,86400);", "", check=False).stderr)

    def test_shared_budget_migration_is_idempotent(self):
        self.spend()
        self.reapply("20261113000001_shared_budget.sql", times=2)
        self.assertEqual(self.spend(), {"allowed": True, "remaining": 1})
        overloads = self.sql("select count(*) from pg_proc where proname in ('pf_spend', 'pf_spend_remaining') and pronamespace = 'public'::regnamespace;").stdout.strip()
        self.assertEqual(overloads, "2")
