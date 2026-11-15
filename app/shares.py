"""Read-only links to a project.

The token is generated here and given to the owner once; Supabase stores only its SHA-256, so neither the
database nor its backups can rebuild a link. A visitor's request is answered with the public key, without
any user token, through the one function anonymous callers may use, and only after a per-address budget.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, field_validator

from .context import ServiceContext, context_of
from .identity import CurrentIdentity
from .project_config import (
    PROJECT_SHARE_DURATIONS_DAYS, PROJECT_SHARE_LIFETIME_DAYS, PROJECT_SHARE_TOKEN_BYTES, PROJECT_SHARE_TOKEN_MAX_LENGTH,
    PROJECT_SHARE_TOKEN_PATTERN,
)
from .projects import Contract, Token, Workspace, data_request

TOKEN = re.compile(PROJECT_SHARE_TOKEN_PATTERN)


class ProjectShare(Contract):
    created_at: datetime
    expires_at: datetime


class CreatedShare(Contract):
    """The token appears in this answer only; afterwards the owner sees the dates, never the link."""

    token: str
    share: ProjectShare


class ShareState(Contract):
    share: ProjectShare | None = None


class ShareRequest(Contract):
    """How long the new link lasts; one of PROJECT_SHARE_DURATIONS_DAYS."""

    duration_days: int = PROJECT_SHARE_LIFETIME_DAYS

    @field_validator("duration_days")
    @classmethod
    def allowed_duration(cls, value: int) -> int:
        if value not in PROJECT_SHARE_DURATIONS_DAYS:
            raise ValueError("Unsupported link duration")
        return value


class SharedRequest(Contract):
    token: str = Field(max_length=PROJECT_SHARE_TOKEN_MAX_LENGTH)


class SharedProject(Contract):
    name: str
    active_sequence: str
    workspace: Workspace
    updated_at: datetime
    expires_at: datetime


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def caller_address(headers, fallback: str | None) -> str:
    """The visitor's address as the platform reports it, for the budget only; it is never stored."""
    forwarded = headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or fallback or "unknown"


def _share(value) -> ProjectShare:
    try:
        return ProjectShare.model_validate(value)
    except ValueError as error:
        raise HTTPException(502, "projects-invalid-response") from error


async def create_share(context: ServiceContext, token: str, project_id: UUID, days: int = PROJECT_SHARE_LIFETIME_DAYS) -> CreatedShare:
    link = secrets.token_urlsafe(PROJECT_SHARE_TOKEN_BYTES)
    stored = await data_request(context, token, "POST", "rpc/pf_share_project", json={"p_id": str(project_id), "p_token_sha256": token_hash(link), "p_days": days})
    return CreatedShare(token=link, share=_share(stored))


async def read_share(context: ServiceContext, token: str, project_id: UUID) -> ShareState:
    stored = await data_request(context, token, "POST", "rpc/pf_project_share", json={"p_id": str(project_id)})
    return ShareState(share=None if stored is None else _share(stored))


async def remove_share(context: ServiceContext, token: str, project_id: UUID) -> None:
    await data_request(context, token, "POST", "rpc/pf_unshare_project", json={"p_id": str(project_id)})


async def open_shared(context: ServiceContext, link: str, address: str) -> SharedProject:
    """A malformed, revoked, expired or unknown link all answer the same 404."""
    if not context.shared_limiter.allow(address):
        raise HTTPException(429, "shared-rate-limit")
    if not TOKEN.fullmatch(link):
        raise HTTPException(404, "shared-not-found")
    # No user token: only the public key travels, and PostgreSQL sees an anonymous caller.
    stored = await data_request(context, None, "POST", "rpc/pf_shared_project", area="shared", json={"p_token_sha256": token_hash(link)})
    if stored is None:
        raise HTTPException(404, "shared-not-found")
    try:
        return SharedProject.model_validate(stored)
    except ValueError as error:
        raise HTTPException(502, "shared-invalid-response") from error


router = APIRouter(tags=["shares"])


@router.post("/projects/{project_id}/share", response_model=CreatedShare, status_code=201)
async def create_share_route(project_id: UUID, request: Request, _identity: CurrentIdentity, token: Token, body: ShareRequest | None = None):
    # An empty body, as older clients send, keeps the default duration.
    return await create_share(context_of(request), token, project_id, (body or ShareRequest()).duration_days)


@router.get("/projects/{project_id}/share", response_model=ShareState)
async def read_share_route(project_id: UUID, request: Request, _identity: CurrentIdentity, token: Token):
    return await read_share(context_of(request), token, project_id)


@router.delete("/projects/{project_id}/share", status_code=204)
async def remove_share_route(project_id: UUID, request: Request, _identity: CurrentIdentity, token: Token):
    await remove_share(context_of(request), token, project_id)
    return Response(status_code=204)


# POST with the token in the body: a path or a query string would leave the link in access logs.
@router.post("/shared", response_model=SharedProject)
async def open_shared_route(body: SharedRequest, request: Request):
    return await open_shared(context_of(request), body.token, caller_address(request.headers, request.client.host if request.client else None))
