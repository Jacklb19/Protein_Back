"""Inspect local configuration or a saved assistant error, without network calls or secret output.

Run from the backend root: python -m scripts.diagnose_assistant --local-config
Only save the response body, never a HAR, request headers or credentials.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from uuid import UUID

from app.config import load_settings

MAX_RESPONSE_BYTES = 2_000_000
DETAILS = {
    "assistant-not-configured": "Check GROQ_API_KEY in the backend deployment environment; a local .env does not configure production.",
    "assistant-rate-limit": "The caller or provider budget was exhausted; this response cannot distinguish the two.",
    "assistant-provider-error": "Inspect the backend provider status for this request; do not share the provider body or key.",
    "assistant-unreachable": "The backend could not reach the provider.",
    "assistant-invalid-response": "The provider response was malformed, empty or outside the size limit.",
    "assistant-incomplete": "The provider did not finish normally; keep truncation validation enabled.",
    "assistant-unverified-figure": "The answer contained an unsupported figure; keep numeric validation enabled.",
    "assistant-timeout": "The provider call timed out.",
    "missing-token": "Sign in before retrying.",
    "invalid-token": "The session was rejected; sign in again.",
}
RPC_CODES = {
    "4": "deadline_exceeded", "8": "resource_exhausted", "9": "failed_precondition",
    "13": "internal", "14": "unavailable", "16": "unauthenticated",
}


def diagnose(body: bytes, protocol: str) -> dict[str, str]:
    """Return only allowlisted diagnostics, never copied provider text or response data."""
    if protocol not in ("rest", "connect", "grpc-web"):
        raise ValueError("Unsupported response protocol")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Response exceeds the diagnostic limit")
    code, detail = "unknown", "unknown"
    if protocol == "grpc-web":
        offset = 0
        while offset < len(body):
            if len(body) - offset < 5:
                raise ValueError("Incomplete gRPC-Web frame")
            flags, length = struct.unpack_from(">BI", body, offset)
            offset += 5
            if length > len(body) - offset or flags not in (0, 0x80):
                raise ValueError("Invalid gRPC-Web frame")
            if flags == 0x80:
                if offset + length != len(body):
                    raise ValueError("Trailers must end the response")
                trailers = {}
                for line in body[offset:offset + length].decode("ascii").split("\r\n"):
                    if not line:
                        continue
                    name, separator, value = line.partition(":")
                    name = name.strip().lower()
                    if not separator or name in trailers:
                        raise ValueError("Invalid or duplicate trailer")
                    trailers[name] = value.strip()
                code = RPC_CODES.get(trailers.get("grpc-status", "").strip(), "unknown")
                detail = trailers.get("grpc-message", "").strip()
            offset += length
    else:
        document = json.loads(body)
        if not isinstance(document, dict):
            raise ValueError("Expected an error object")
        detail = document.get("message" if protocol == "connect" else "detail")
        raw_code = document.get("code") if protocol == "connect" else None
        if isinstance(raw_code, str) and raw_code in RPC_CODES.values():
            code = raw_code
    if not isinstance(detail, str) or detail not in DETAILS:
        detail = "unknown"
    return {"code": code, "detail": detail, "next_step": DETAILS.get(detail, "No recognized assistant error; inspect only its stable code and request reference.")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-config", action="store_true", help="Report presence only; does not check production or contact Groq")
    parser.add_argument("--response", type=Path, help="Saved response body only, never a HAR")
    parser.add_argument("--protocol", choices=("connect", "rest", "grpc-web"), default="grpc-web")
    parser.add_argument("--request-id", help="Optional UUID reference, never a token")
    args = parser.parse_args()
    if not args.local_config and args.response is None:
        parser.error("Choose --local-config or --response")
    result = {}
    if args.request_id is not None:
        try:
            result["request_id"] = str(UUID(args.request_id))
        except ValueError:
            parser.exit(2, "Request reference must be a UUID; no input data was printed.\n")
    if args.local_config:
        try:
            settings = load_settings()
        except ValueError:
            parser.exit(2, "Invalid local configuration; no setting values were printed.\n")
        result["local_config"] = {"assistant_configured": settings.assistant_enabled, "identity_configured": settings.identity_enabled}
    if args.response is not None:
        try:
            with args.response.open("rb") as source:
                result["response"] = diagnose(source.read(MAX_RESPONSE_BYTES + 1), args.protocol)
        except (OSError, ValueError, UnicodeError):
            parser.exit(2, "Cannot inspect this response body; no input data was printed.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
