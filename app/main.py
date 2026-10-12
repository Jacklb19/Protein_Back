"""Application factory. Run locally with `uvicorn app.main:app --reload`."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import API_PREFIX, REQUEST_ID_HEADER, Settings, load_settings
from .identity import CurrentIdentity
from .projects import router as projects_router
from .problems import http_exception_handler, unexpected_exception_handler, validation_exception_handler

API_TITLE = "ProteinForge API"
API_VERSION = "0.1.0"


class Health(BaseModel):
    status: str = Field(description="`ok` while the process answers requests.")
    version: str
    identity: bool = Field(description="Whether identity validation is configured.")


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

    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)
    app.state.settings = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)
    app.include_router(projects_router, prefix=API_PREFIX)

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request.state.request_id = incoming if 0 < len(incoming) <= 64 else uuid.uuid4().hex
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get(f"{API_PREFIX}/health", response_model=Health, tags=["service"])
    async def health() -> Health:
        return Health(status="ok", version=API_VERSION, identity=resolved.identity_enabled)

    @app.get(f"{API_PREFIX}/me", response_model=Me, tags=["identity"])
    async def me(identity: CurrentIdentity) -> Me:
        return Me(id=identity.id, email=identity.email)

    return app


app = create_app()
