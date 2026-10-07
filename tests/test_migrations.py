"""Audit explicit RLS activation in versioned Supabase migrations."""

from pathlib import Path
import re
import unittest

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


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


if __name__ == "__main__":
    unittest.main()
