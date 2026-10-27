"""Offline diagnosis must recognize wire errors without printing captured private data."""

import io
import json
import struct
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from app.config import Settings
from scripts.diagnose_assistant import MAX_RESPONSE_BYTES, diagnose, main


def frame(flags, value):
    return struct.pack(">BI", flags, len(value)) + value


class AssistantDiagnosticTests(unittest.TestCase):
    def test_missing_configuration_is_recognized_in_both_rpc_protocols(self):
        connect = diagnose(b'{"code":"failed_precondition","message":"assistant-not-configured"}', "connect")
        web = diagnose(frame(0x80, b"grpc-status: 9\r\ngrpc-message: assistant-not-configured\r\n"), "grpc-web")
        self.assertEqual(connect, web)
        self.assertEqual(web["detail"], "assistant-not-configured")

    def test_distinguishes_provider_and_numeric_errors_without_showing_data_frames(self):
        for detail in ("assistant-provider-error", "assistant-unverified-figure", "assistant-incomplete"):
            body = frame(0, b"private-sequence-and-provider-text") + frame(0x80, f"grpc-status: 14\r\ngrpc-message: {detail}\r\n".encode())
            result = diagnose(body, "grpc-web")
            self.assertEqual(result["detail"], detail)
            self.assertNotIn("private", json.dumps(result))

    def test_rest_never_echoes_extra_fields_or_unknown_details(self):
        result = diagnose(json.dumps({"detail": "assistant-timeout", "secret": "gsk_secret"}).encode(), "rest")
        self.assertEqual(result["detail"], "assistant-timeout")
        self.assertNotIn("gsk_secret", json.dumps(result))
        for value in ("gsk_secret", ["private"], {"private": True}):
            result = diagnose(json.dumps({"code": "private", "message": value}).encode(), "connect")
            self.assertEqual(result["code"], "unknown")
            self.assertEqual(result["detail"], "unknown")
            self.assertNotIn("private", json.dumps(result))

    def test_rejects_malformed_and_oversized_bodies(self):
        cases = [
            (b"bad", "grpc-web"),
            (frame(0x80, b"grpc-status: 9")[:-1], "grpc-web"),
            (frame(1, b"compressed"), "grpc-web"),
            (frame(0x80, b"grpc-status: 9") + frame(0, b"late"), "grpc-web"),
            (frame(0x80, b"grpc-status: 9\r\ngrpc-status: 14\r\n"), "grpc-web"),
            (b"[]", "rest"),
            (b"x" * (MAX_RESPONSE_BYTES + 1), "rest"),
        ]
        for body, protocol in cases:
            with self.subTest(protocol=protocol, length=len(body)), self.assertRaises(ValueError):
                diagnose(body, protocol)

    def test_local_preflight_reports_only_presence_and_never_calls_the_provider(self):
        output = io.StringIO()
        with patch("sys.argv", ["diagnose_assistant", "--local-config"]), patch("scripts.diagnose_assistant.load_settings", return_value=Settings(groq_api_key="gsk_secret")), patch("httpx.AsyncClient.post") as post, redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue()), {"local_config": {"assistant_configured": True, "identity_configured": False}})
        post.assert_not_called()
        self.assertNotIn("gsk_secret", output.getvalue())

    def test_invalid_reference_does_not_echo_the_submitted_value(self):
        output = io.StringIO()
        with patch("sys.argv", ["diagnose_assistant", "--local-config", "--request-id", "gsk_secret"]), redirect_stderr(output), self.assertRaises(SystemExit):
            main()
        self.assertNotIn("gsk_secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
