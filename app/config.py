"""Server settings read from environment variables; nothing is hard-coded in routes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

API_PREFIX = "/v1"
SUPABASE_AUTH_PATH = "/auth/v1"
REQUEST_ID_HEADER = "X-Request-Id"

# Request headers that browsers send for Connect and gRPC-Web calls, and gRPC status headers they must read.
RPC_REQUEST_HEADERS = ("Connect-Protocol-Version", "Connect-Timeout-Ms", "Grpc-Timeout", "X-Grpc-Web", "X-User-Agent")
RPC_RESPONSE_HEADERS = ("Grpc-Status", "Grpc-Message", "Grpc-Status-Details-Bin")
DEFAULT_IDENTITY_TIMEOUT_SECONDS = 5.0
# Queries one signed-in caller may forward to UniProt each minute, per process. 0 disables the budget.
DEFAULT_UNIPROT_REQUESTS_PER_MINUTE = 20
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class Settings:
    """Public and secret configuration. Secrets never leave the process or appear in logs."""

    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    supabase_url: str = ""
    supabase_anon_key: str = ""
    identity_timeout_seconds: float = DEFAULT_IDENTITY_TIMEOUT_SECONDS
    uniprot_requests_per_minute: int = DEFAULT_UNIPROT_REQUESTS_PER_MINUTE

    @property
    def identity_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def identity_user_url(self) -> str:
        return f"{self.supabase_url}{SUPABASE_AUTH_PATH}/user"


def _origins(raw: str) -> tuple[str, ...]:
    return tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())


def load_settings(environment: dict[str, str] | None = None) -> Settings:
    """Read this repository's .env, with process overrides; explicit mappings stay isolated."""
    env = environment if environment is not None else {
        **{key: value for key, value in dotenv_values(ENV_FILE).items() if value is not None},
        **os.environ,
    }
    origins = _origins(env.get("ALLOWED_ORIGINS", "")) or _origins(env.get("FRONTEND_ORIGIN", ""))
    timeout = env.get("IDENTITY_TIMEOUT_SECONDS", "")
    budget = env.get("UNIPROT_REQUESTS_PER_MINUTE", "")
    return Settings(
        allowed_origins=origins,
        supabase_url=env.get("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_anon_key=env.get("SUPABASE_ANON_KEY", "").strip(),
        identity_timeout_seconds=float(timeout) if timeout else DEFAULT_IDENTITY_TIMEOUT_SECONDS,
        uniprot_requests_per_minute=int(budget) if budget else DEFAULT_UNIPROT_REQUESTS_PER_MINUTE,
    )
