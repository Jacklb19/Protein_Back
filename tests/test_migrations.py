"""Audit explicit RLS activation in versioned Supabase migrations."""

from pathlib import Path
import re
import unittest
from typing import get_args

from app import project_config
from app.projects import MAX_SEQUENCE_LENGTH, Sequence, Workspace
from app.rpc.convert import STATUSES

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
TABLE_CONSTRAINTS = MIGRATIONS / "20261102000001_table_write_constraints.sql"


def quoted(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


class MigrationSecurityTests(unittest.TestCase):
    def test_includes_at_least_one_sql_migration(self):
        self.assertTrue(MIGRATIONS.is_dir())
        self.assertTrue(list(MIGRATIONS.glob("*.sql")))

    def test_enables_row_level_security_for_every_created_table(self):
        created = set()
        enabled = set()
        create_pattern = r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?([a-zA-Z0-9_]+)\s*\("
        rls_pattern = r"alter\s+table\s+(?:public\.)?([a-zA-Z0-9_]+)\s+enable\s+row\s+level\s+security\s*;"
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            content = migration.read_text(encoding="utf-8")
            created.update(name.lower() for name in re.findall(create_pattern, content, re.IGNORECASE))
            enabled.update(name.lower() for name in re.findall(rls_pattern, content, re.IGNORECASE))
        self.assertTrue(created)
        for table in sorted(created):
            with self.subTest(table=table):
                self.assertIn(table, enabled, f"Table '{table}' was created without row level security")


class TableConstraintTests(unittest.TestCase):
    """Direct PostgREST writes must meet the bounds the API enforces; the values come from the code."""

    def setUp(self):
        self.sql = TABLE_CONSTRAINTS.read_text(encoding="utf-8")

    def test_constraints_mirror_the_api_limits(self):
        pattern = get_args(Sequence)[1].pattern
        versions = get_args(Workspace.model_fields["schema_version"].annotation)
        self.assertIn(f"length(name) between 1 and {project_config.PROJECT_NAME_LENGTH}", self.sql)
        self.assertIn(f"length(active_sequence) <= {MAX_SEQUENCE_LENGTH} and active_sequence ~ '{pattern}'", self.sql)
        self.assertIn(f"status in ({quoted(STATUSES)})", self.sql)
        self.assertIn(f"results->>'schemaVersion' in ({quoted(versions)})", self.sql)
        # The snapshot bound is the one the newest pf_save_project enforces.
        save = [path for path in sorted(MIGRATIONS.glob("*.sql")) if "function public.pf_save_project(" in path.read_text(encoding="utf-8")][-1]
        limit = re.search(r"octet_length\(p_workspace::text\) > (\d+)", save.read_text(encoding="utf-8")).group(1)
        self.assertIn(f"octet_length(results::text) <= {limit}", self.sql)

    def test_constraints_are_not_valid_added_once_and_grants_stay_for_invoker_functions(self):
        names = re.findall(r"\('public\.\w+'::regclass, '(\w+)'", self.sql)
        self.assertEqual(len(names), 6)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("add constraint %I %s not valid", self.sql)
        self.assertIn("if not exists (select 1 from pg_constraint where conrelid = item.table_name and conname = item.constraint_name)", self.sql)
        self.assertNotRegex(self.sql, r"(?i)drop\s+constraint")
        # pf_save_project is SECURITY INVOKER: projects and analyses must stay writable by authenticated.
        self.assertNotRegex(self.sql, r"(?i)revoke[^;]*on\s+public\.(projects|analyses)\b")
        self.assertIn("revoke insert, update on public.alignments from authenticated;", self.sql)


if __name__ == "__main__":
    unittest.main()
