"""Application factory. Run locally with `uvicorn app.main:app --reload`."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import API_DOCS_PATHS, API_PREFIX, ASSISTANT_REMAINING_HEADER, REQUEST_ID_HEADER, RPC_REQUEST_HEADERS, RPC_RESPONSE_HEADERS, Settings, load_settings
from .correlation import Correlation
from .security_headers import SecurityHeaders
from .identity import CurrentIdentity
from .projects import router as projects_router
from .reports import router as reports_router
from .uniprot import router as uniprot_router
from .assistant import router as assistant_router
from .assistant_cache import AssistantCache
from .residue_mapping import router as residue_mapping_router
from .sifts_cache import SiftsCache
from .folding import FoldingGate, router as folding_router
from .live import LiveHub, router as live_router
from .shares import router as shares_router
from .rpc.router import RpcRouter, rpc_applications
from .rate_limit import FixedWindowLimiter
from .spend_config import DAY_SECONDS
from .problems import http_exception_handler, unexpected_exception_handler, validation_exception_handler

API_TITLE = "ProteinForge API"
API_VERSION = "0.1.0"


class Health(BaseModel):
    status: str = Field(description="`ok` while the process answers requests.")
    version: str
    identity: bool = Field(description="Whether identity validation is configured.")
    live: bool = Field(description="Whether the live channel (/v1/live) accepts connections.")


class Me(BaseModel):
    id: str
    email: str


def create_app(settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    """Builds the API with explicit settings; tests pass a client with a mock transport."""
    resolved = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        client = http_client if http_client is not None else httpx.AsyncClient()
        application.state.settings = resolved
        application.state.http_client = client
        try:
            yield
        finally:
            if http_client is None:
                await client.aclose()

    # Production does not publish its schema; local development opts in with API_DOCS_ENABLED.
    docs = API_DOCS_PATHS if resolved.api_docs_enabled else dict.fromkeys(API_DOCS_PATHS)
    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan, **docs)
    app.state.settings = resolved
    # Shared by REST and RPC: one budget per signed-in caller. With SHARED_BUDGET_ENABLED PostgreSQL counts it
    # (app/spend.py) and these per-process counters only decide while the database cannot answer.
    app.state.uniprot_limiter = FixedWindowLimiter(resolved.uniprot_requests_per_minute)
    app.state.assistant_limiter = FixedWindowLimiter(resolved.assistant_requests_per_minute)
    app.state.assistant_daily_limiter = FixedWindowLimiter(resolved.assistant_requests_per_day, DAY_SECONDS, max_callers=1)
    app.state.assistant_cache = AssistantCache(resolved.assistant_cache_ttl_seconds, resolved.assistant_cache_max_entries)
    app.state.sifts_cache = SiftsCache()
    app.state.folding_gate = FoldingGate()
    app.state.live_hub = LiveHub(resolved.live.connections_per_caller)
    app.state.shared_limiter = FixedWindowLimiter(resolved.shared_requests_per_minute)
    # RPC services share the app state with REST; added first so CORS wraps them too.
    app.add_middleware(RpcRouter, applications=rpc_applications(app.state, API_VERSION))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER, *RPC_REQUEST_HEADERS],
        expose_headers=[REQUEST_ID_HEADER, ASSISTANT_REMAINING_HEADER, *RPC_RESPONSE_HEADERS],
    )
    # Outermost, so REST, RPC and CORS answers all carry the request id.
    app.add_middleware(Correlation)
    # Around everything else, so REST, RPC, CORS and error answers carry the hardening headers.
    app.add_middleware(SecurityHeaders)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)
    app.include_router(projects_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(uniprot_router, prefix=API_PREFIX)
    app.include_router(assistant_router, prefix=API_PREFIX)
    app.include_router(residue_mapping_router, prefix=API_PREFIX)
    app.include_router(folding_router, prefix=API_PREFIX)
    app.include_router(live_router, prefix=API_PREFIX)
    app.include_router(shares_router, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health", response_model=Health, tags=["service"])
    async def health() -> Health:
        return Health(status="ok", version=API_VERSION, identity=resolved.identity_enabled, live=resolved.live_enabled and resolved.identity_enabled)

    @app.get(f"{API_PREFIX}/me", response_model=Me, tags=["identity"])
    async def me(identity: CurrentIdentity) -> Me:
        return Me(id=identity.id, email=identity.email)

    return app


app = create_app()
