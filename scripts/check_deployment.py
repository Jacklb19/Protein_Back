"""Scheduled check of a deployed API: the service answers and an anonymous read reaches the database.

Standard library only, so the workflow needs no install step. No secrets: the health route is public and
the database read is the anonymous shared-link lookup with a well-formed token that cannot exist, which
must travel to PostgreSQL (pf_shared_project) and come back as 404 shared-not-found. Any other answer,
such as a 503 from a paused database, fails the check.

Usage: python scripts/check_deployment.py https://protein-back.vercel.app
"""

from __future__ import annotations

import json
import secrets
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

HEALTH_PATH = "/v1/health"
SHARED_PATH = "/v1/shared"
# Same shape as a real link token (app/project_config.py: 32 random bytes, 43 URL-safe characters).
SHARED_TOKEN_BYTES = 32
EXPECTED_SHARED_STATUS = 404
EXPECTED_SHARED_CODE = "shared-not-found"
TIMEOUT_SECONDS = 20
USER_AGENT = "proteinforge-deployment-check"


@dataclass(frozen=True)
class Answer:
    status: int
    body: object


Fetch = Callable[[str, str, dict | None], Answer]


def fetch(url: str, method: str, payload: dict | None) -> Answer:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return Answer(response.status, _json(response.read()))
    except urllib.error.HTTPError as error:
        return Answer(error.code, _json(error.read()))


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def check(base_url: str, call: Fetch = fetch, token: str | None = None) -> list[str]:
    """Problems found; an empty list means both answers were the expected ones."""
    base = base_url.rstrip("/")
    problems: list[str] = []
    health = call(f"{base}{HEALTH_PATH}", "GET", None)
    body = health.body if isinstance(health.body, dict) else {}
    if health.status != 200 or body.get("status") != "ok":
        problems.append(f"health answered {health.status}, expected 200 with status ok")
    elif body.get("identity") is not True:
        problems.append("health reports identity is not configured")
    shared = call(f"{base}{SHARED_PATH}", "POST", {"token": token or secrets.token_urlsafe(SHARED_TOKEN_BYTES)})
    kind = shared.body.get("type", "") if isinstance(shared.body, dict) else ""
    if shared.status != EXPECTED_SHARED_STATUS or not str(kind).endswith(EXPECTED_SHARED_CODE):
        problems.append(f"shared lookup answered {shared.status} {kind or 'without a problem type'}, expected {EXPECTED_SHARED_STATUS} {EXPECTED_SHARED_CODE}")
    return problems


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        problems = check(arguments[0])
    except (urllib.error.URLError, TimeoutError) as error:
        problems = [f"request failed: {error}"]
    for line in problems:
        print(f"::error::{line}")
    if not problems:
        print("Health and anonymous database read answered as expected.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
