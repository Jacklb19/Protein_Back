"""Local configuration loading without leaking values or changing the process environment."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import DEFAULT_UNIPROT_REQUESTS_PER_MINUTE, load_settings


class DotenvTests(unittest.TestCase):
    def test_loads_repository_file_without_changing_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                'SUPABASE_URL="https://local.supabase.co/"\n'
                'SUPABASE_ANON_KEY="public-key"\n'
                'ALLOWED_ORIGINS="http://127.0.0.1:5173"\n',
                encoding="utf-8",
            )
            with patch("app.config.ENV_FILE", path), patch.dict(os.environ, {}, clear=True):
                settings = load_settings()
                self.assertNotIn("SUPABASE_ANON_KEY", os.environ)
        self.assertTrue(settings.identity_enabled)
        self.assertEqual(settings.identity_user_url, "https://local.supabase.co/auth/v1/user")
        self.assertEqual(settings.allowed_origins, ("http://127.0.0.1:5173",))

    def test_process_values_override_file_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SUPABASE_URL=https://file.supabase.co\nSUPABASE_ANON_KEY=file-key\n", encoding="utf-8")
            with patch("app.config.ENV_FILE", path), patch.dict(os.environ, {
                "SUPABASE_URL": "https://process.supabase.co",
                "SUPABASE_ANON_KEY": "process-key",
            }, clear=True):
                settings = load_settings()
        self.assertEqual(settings.supabase_url, "https://process.supabase.co")
        self.assertEqual(settings.supabase_anon_key, "process-key")

    def test_explicit_mapping_never_reads_file_or_process(self):
        with patch("app.config.dotenv_values") as read_file, patch.dict(os.environ, {
            "SUPABASE_URL": "https://process.supabase.co", "SUPABASE_ANON_KEY": "process-key",
        }):
            settings = load_settings({})
        read_file.assert_not_called()
        self.assertFalse(settings.identity_enabled)

    def test_missing_file_remains_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.config.ENV_FILE", Path(directory) / "absent.env"), patch.dict(os.environ, {}, clear=True):
                self.assertFalse(load_settings().identity_enabled)

    def test_provider_budget_defaults_and_reads_the_environment(self):
        self.assertEqual(load_settings({}).uniprot_requests_per_minute, DEFAULT_UNIPROT_REQUESTS_PER_MINUTE)
        self.assertEqual(load_settings({"UNIPROT_REQUESTS_PER_MINUTE": "5"}).uniprot_requests_per_minute, 5)
        # Zero is the explicit way to run without a budget; an empty value keeps the default.
        self.assertEqual(load_settings({"UNIPROT_REQUESTS_PER_MINUTE": "0"}).uniprot_requests_per_minute, 0)
        self.assertEqual(load_settings({"UNIPROT_REQUESTS_PER_MINUTE": ""}).uniprot_requests_per_minute, DEFAULT_UNIPROT_REQUESTS_PER_MINUTE)
