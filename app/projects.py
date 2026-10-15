"""Personal workspaces. Supabase receives the user's JWT, so PostgreSQL RLS stays in force."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.alias_generators import to_camel

from .context import ServiceContext, context_of
from .identity import CurrentIdentity, bearer_token

MAX_SEQUENCE_LENGTH = 100_000
MAX_ALIGNMENT_LENGTH = 5_000
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000
DATA_TIMEOUT_SECONDS = 10.0
Sequence = Annotated[str, StringConstraints(max_length=MAX_SEQUENCE_LENGTH, pattern=r"^[ACDEFGHIKLMNPQRSTVWYUOBZX]*$")]
AlignmentSequence = Annotated[str, StringConstraints(max_length=MAX_ALIGNMENT_LENGTH, pattern=r"^[ACDEFGHIKLMNPQRSTVWYUOBZX]*$")]
Token = Annotated[str, Depends(bearer_token)]


class Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", allow_inf_nan=False)


class DescriptorResult(Contract):
    mass_da: float = Field(gt=0)
    isoelectric_point: float = Field(ge=0, le=14)
    instability_index: float
    aliphatic_index: float = Field(ge=0)
    gravy: float = Field(ge=-4.5, le=4.5)
    length: int = Field(gt=0, le=MAX_SEQUENCE_LENGTH)


class DescriptorSnapshot(Contract):
    sequence: Sequence
    excluded: int = Field(ge=0)
    method_version: Literal["proteinforge-descriptors-v1"]
    result: DescriptorResult


class AlignmentParameters(Contract):
    matrix: Literal["BLOSUM45", "BLOSUM62", "BLOSUM80"]
    mode: Literal["global", "local"]
    gap_open: Literal[10]
    gap_extend: Literal[0.5]
    terminal_gaps: Literal["free", "not-applicable"]


class AlignmentResult(Contract):
    aligned_first: str = Field(max_length=MAX_ALIGNMENT_LENGTH * 2, pattern=r"^[ACDEFGHIKLMNPQRSTVWYBZX-]+$")
    aligned_second: str = Field(max_length=MAX_ALIGNMENT_LENGTH * 2, pattern=r"^[ACDEFGHIKLMNPQRSTVWYBZX-]+$")
    marks: str = Field(max_length=MAX_ALIGNMENT_LENGTH * 2, pattern=r"^[|: ]+$")
    score: float
    identities: int = Field(ge=0)
    similarities: int = Field(ge=0)
    columns: int = Field(gt=0, le=MAX_ALIGNMENT_LENGTH * 2)
    parameters: AlignmentParameters

    @model_validator(mode="after")
    def validate_columns(self):
        if any(len(value) != self.columns for value in (self.aligned_first, self.aligned_second, self.marks)):
            raise ValueError("Inconsistent alignment columns")
        if not self.identities <= self.similarities <= self.columns:
            raise ValueError("Inconsistent alignment counts")
        if self.identities != self.marks.count("|") or self.similarities != self.identities + self.marks.count(":"):
            raise ValueError("Inconsistent conservation marks")
        if self.parameters.terminal_gaps != ("free" if self.parameters.mode == "global" else "not-applicable"):
            raise ValueError("Inconsistent terminal gap policy")
        return self


class AlignmentSnapshot(Contract):
    first: AlignmentSequence = ""
    second: AlignmentSequence = ""
    matrix: Literal["BLOSUM45", "BLOSUM62", "BLOSUM80"] = "BLOSUM62"
    mode: Literal["global", "local"] = "global"
    result: AlignmentResult | None = None

    @model_validator(mode="after")
    def validate_result(self):
        if self.result:
            params = self.result.parameters
            if params.matrix != self.matrix or params.mode != self.mode or not self.first or not self.second:
                raise ValueError("Stale alignment")
            normalized = lambda value: value.replace("U", "C").replace("O", "K")
            for aligned, sequence in ((self.result.aligned_first, self.first), (self.result.aligned_second, self.second)):
                ungapped = aligned.replace("-", "")
                if self.mode == "global" and ungapped != normalized(sequence):
                    raise ValueError("Alignment does not match its input")
                if self.mode == "local" and ungapped not in normalized(sequence):
                    raise ValueError("Local alignment does not match its input")
        return self


class SequenceSource(Contract):
    origin: Literal["catalog", "uniprot"]
    accession: str = Field(pattern=r"^[A-Z0-9]{6,10}$")
    sequence: Sequence
    entry_version: int | None = Field(default=None, gt=0)
    sequence_version: int | None = Field(default=None, gt=0)
    retrieved_at: str | None = Field(default=None, max_length=64)

    @field_validator("retrieved_at")
    @classmethod
    def require_source_timestamp(cls, value: str | None) -> str | None:
        if value is not None and datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("Timezone required")
        return value


class Workspace(Contract):
    schema_version: Literal[1] = 1
    profile_window: Literal[9, 19] = 9
    source: SequenceSource | None = None
    descriptors: DescriptorSnapshot | None = None
    alignment: AlignmentSnapshot = Field(default_factory=AlignmentSnapshot)


class ProjectWrite(Contract):
    name: str = Field(min_length=1, max_length=120)
    active_sequence: Sequence = ""
    workspace: Workspace = Field(default_factory=Workspace)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Invalid project name")
        return value

    @model_validator(mode="after")
    def validate_descriptors(self):
        snapshot = self.workspace.descriptors
        if self.workspace.source and self.workspace.source.sequence != self.active_sequence:
            raise ValueError("Stale source")
        if snapshot:
            excluded = sum(residue in "UOBZX" for residue in self.active_sequence)
            if snapshot.sequence != self.active_sequence or snapshot.excluded != excluded or snapshot.result.length != len(self.active_sequence) - excluded:
                raise ValueError("Stale descriptors")
        return self


class ProjectUpdate(ProjectWrite):
    expected_updated_at: datetime

    @field_validator("expected_updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value


class ProjectSummary(Contract):
    id: UUID
    name: str
    status: str
    created_at: str
    updated_at: str


class Project(ProjectSummary):
    active_sequence: Sequence
    workspace: Workspace


class ProjectList(Contract):
    items: list[ProjectSummary]
    next_offset: int | None


router = APIRouter(prefix="/projects", tags=["projects"])


async def data_request(context: ServiceContext, token: str, method: str, path: str, **kwargs):
    settings = context.settings
    headers = {"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = await context.http_client.request(method, f"{settings.supabase_url}/rest/v1/{path}", headers=headers, timeout=DATA_TIMEOUT_SECONDS, **kwargs)
    except httpx.TimeoutException as error:
        raise HTTPException(504, "projects-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "projects-unreachable") from error
    if response.status_code == 401:
        raise HTTPException(401, "invalid-token", headers={"WWW-Authenticate": "Bearer"})
    if response.status_code >= 400:
        try:
            code = response.json().get("code", "")
        except (ValueError, AttributeError):
            code = ""
        if code in ("PGRST202", "PGRST205", "42P01", "42883"):
            raise HTTPException(503, "projects-schema-unavailable")
        if code == "PT409":
            raise HTTPException(409, "project-conflict")
        if code == "PT404":
            raise HTTPException(404, "project-not-found")
        if response.status_code == 403:
            raise HTTPException(403, "project-forbidden")
        raise HTTPException(502, "projects-provider-error")
    try:
        return response.json()
    except ValueError as error:
        raise HTTPException(502, "projects-invalid-response") from error


def document(value) -> Project:
    if value is None:
        raise HTTPException(404, "project-not-found")
    try:
        return Project.model_validate(value)
    except ValueError as error:
        raise HTTPException(502, "projects-invalid-response") from error


async def fetch_projects(context: ServiceContext, owner_id: str, token: str, limit: int, offset: int) -> ProjectList:
    """Owned project summaries, most recently updated first; `limit + 1` rows tell whether more remain."""
    rows = await data_request(context, token, "GET", "projects", params={
        "owner_id": f"eq.{owner_id}", "select": "id,name,status,created_at,updated_at", "order": "updated_at.desc,id.desc", "limit": limit + 1, "offset": offset,
    })
    try:
        if not isinstance(rows, list):
            raise ValueError("Invalid listing")
        items = [ProjectSummary.model_validate(row) for row in rows[:limit]]
    except ValueError as error:
        raise HTTPException(502, "projects-invalid-response") from error
    return ProjectList(items=items, next_offset=offset + limit if len(rows) > limit else None)


async def fetch_project(context: ServiceContext, token: str, project_id: UUID) -> Project:
    return document(await data_request(context, token, "POST", "rpc/pf_project_document", json={"p_id": str(project_id)}))


async def remove_project(context: ServiceContext, token: str, project_id: UUID, expected: datetime) -> None:
    if expected.tzinfo is None:
        raise HTTPException(422, "validation")
    await data_request(context, token, "POST", "rpc/pf_delete_project", json={"p_id": str(project_id), "p_expected_updated_at": expected.isoformat()})


@router.get("", response_model=ProjectList)
async def list_projects(request: Request, identity: CurrentIdentity, token: Token, limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE), offset: int = Query(0, ge=0, le=MAX_OFFSET)):
    return await fetch_projects(context_of(request), identity.id, token, limit, offset)


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: UUID, request: Request, _identity: CurrentIdentity, token: Token):
    return await fetch_project(context_of(request), token, project_id)


async def save_project(context: ServiceContext, token: str, payload: ProjectWrite, project_id: UUID | None = None, expected: datetime | None = None) -> Project:
    return document(await data_request(context, token, "POST", "rpc/pf_save_project", json={
        "p_id": str(project_id) if project_id else None,
        "p_name": payload.name,
        "p_sequence": payload.active_sequence,
        "p_workspace": payload.workspace.model_dump(by_alias=True),
        "p_expected_updated_at": expected.isoformat() if expected else None,
    }))


@router.post("", response_model=Project, status_code=201)
async def create_project(payload: ProjectWrite, request: Request, _identity: CurrentIdentity, token: Token):
    return await save_project(context_of(request), token, payload)


@router.put("/{project_id}", response_model=Project)
async def update_project(project_id: UUID, payload: ProjectUpdate, request: Request, _identity: CurrentIdentity, token: Token):
    return await save_project(context_of(request), token, payload, project_id, payload.expected_updated_at)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: UUID, request: Request, _identity: CurrentIdentity, token: Token, expected_updated_at: Annotated[datetime, Query(alias="expectedUpdatedAt")]):
    await remove_project(context_of(request), token, project_id, expected_updated_at)
    return Response(status_code=204)
