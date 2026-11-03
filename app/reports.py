"""Reports generated in the browser and stored in Supabase Storage. The API never receives file bytes.

PostgREST and Storage both receive the user's JWT, so row-level security on public.reports and on
storage.objects decides ownership. The API reserves a record, signs URLs and keeps the record in step.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from .config import SUPABASE_STORAGE_PATH
from .context import ServiceContext, context_of
from .identity import CurrentIdentity, Identity, bearer_token
from .projects import DATA_TIMEOUT_SECONDS, data_request
from .report_config import (
    DEFAULT_REPORT_PAGE_SIZE, DOWNLOAD_URL_SECONDS, MAX_REPORT_BYTES, MAX_REPORT_OFFSET, MAX_REPORT_PAGE_SIZE,
    REPORT_BUCKET, REPORT_KINDS, REPORT_RPC, UPLOAD_URL_SECONDS,
)

logger = logging.getLogger(__name__)

AREA = "reports"
SHA256_PATTERN = r"^[a-f0-9]{64}$"
# Fixed messages of the report SQL functions (PT codes); never contain submitted data.
SQL_MESSAGES = {
    "Report not found": (404, "report-not-found"),
    "Project not found": (404, "project-not-found"),
    "Report upload missing": (409, "report-upload-missing"),
    "Report quota exceeded": (413, "report-quota-exceeded"),
    "Invalid report": (422, "validation"),
    "Invalid report query": (422, "validation"),
}
ReportKind = Literal[tuple(REPORT_KINDS)]
Token = Annotated[str, Depends(bearer_token)]


class Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", allow_inf_nan=False)


class ReportUploadRequest(Contract):
    project_id: UUID | None = None
    kind: ReportKind
    size_bytes: int = Field(ge=1, le=MAX_REPORT_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)


class Report(Contract):
    id: UUID
    project_id: UUID | None = None
    kind: ReportKind
    size_bytes: int = Field(ge=1, le=MAX_REPORT_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)
    status: Literal["pending", "ready"]
    created_at: datetime
    completed_at: datetime | None = None


class ReportUpload(Contract):
    report: Report
    upload_url: str
    content_type: str
    expires_at: datetime


class ReportDownload(Contract):
    report: Report
    download_url: str
    file_name: str
    expires_at: datetime


class ReportList(Contract):
    items: list[Report]
    next_offset: int | None


class StoredReport(Contract):
    """A record with its internal object path, which must sit in the caller's folder."""
    report: Report
    object_path: str

    def checked(self, identity: Identity) -> StoredReport:
        extension = REPORT_KINDS[self.report.kind][1]
        if self.object_path != f"{identity.id}/{self.report.id}.{extension}":
            raise ValueError("Unexpected object path")
        return self


def _invalid(error: Exception) -> HTTPException:
    if isinstance(error, ValidationError):
        # Field paths only, never the stored values.
        logger.warning("Stored report does not match the contract: %s", sorted({".".join(map(str, item["loc"])) for item in error.errors()}))
    return HTTPException(502, f"{AREA}-invalid-response")


def _report(value: Any) -> Report:
    try:
        return Report.model_validate(value)
    except ValueError as error:
        raise _invalid(error) from error


def _stored(value: Any, identity: Identity) -> StoredReport:
    if value is None:
        raise HTTPException(404, "report-not-found")
    try:
        return StoredReport.model_validate(value).checked(identity)
    except ValueError as error:
        raise _invalid(error) from error


async def _data(context: ServiceContext, token: str, rpc: str, body: dict[str, Any]) -> Any:
    return await data_request(context, token, "POST", REPORT_RPC[rpc], area=AREA, messages=SQL_MESSAGES, json=body)


async def storage_request(context: ServiceContext, token: str, method: str, path: str, body: dict[str, Any], missing: tuple[int, str]) -> Any:
    """Storage API call with the user's token, so Storage applies the storage.objects policies."""
    settings = context.settings
    headers = {"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = await context.http_client.request(method, f"{settings.supabase_url}{SUPABASE_STORAGE_PATH}/{path}", headers=headers, json=body, timeout=DATA_TIMEOUT_SECONDS)
    except httpx.TimeoutException as error:
        raise HTTPException(504, f"{AREA}-storage-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, f"{AREA}-storage-unreachable") from error
    try:
        payload = response.json()
    except ValueError:
        payload = None
    status = response.status_code
    # Some Storage versions answer 400 and carry the real status in the body.
    if status >= 400 and isinstance(payload, dict) and str(payload.get("statusCode", "")).isdigit():
        status = int(payload["statusCode"])
    if status < 400:
        return payload
    if status == 401:
        raise HTTPException(401, "invalid-token", headers={"WWW-Authenticate": "Bearer"})
    if status == 403:
        raise HTTPException(403, "report-forbidden")
    if status == 404:
        raise HTTPException(*missing)
    if status == 409:
        raise HTTPException(409, "report-conflict")
    # Status only: Storage messages can quote object paths.
    logger.warning("Supabase Storage request failed: %s %s -> %s", method, path.split("/", 1)[0], status)
    raise HTTPException(502, f"{AREA}-storage-error")


def _signed_url(context: ServiceContext, payload: Any, field: str, prefix: str) -> str:
    """Absolute URL from Storage's relative signed path; anything unexpected is rejected."""
    value = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.startswith(prefix) or "token=" not in value:
        raise HTTPException(502, f"{AREA}-invalid-response")
    return f"{context.settings.supabase_url}{SUPABASE_STORAGE_PATH}{value}"


async def reserve_report(context: ServiceContext, identity: Identity, token: str, payload: ReportUploadRequest) -> ReportUpload:
    stored = _stored(await _data(context, token, "reserve", {
        "p_project_id": str(payload.project_id) if payload.project_id else None,
        "p_kind": payload.kind, "p_size_bytes": payload.size_bytes, "p_sha256": payload.sha256,
    }), identity)
    route = f"object/upload/sign/{REPORT_BUCKET}/{stored.object_path}"
    try:
        signed = await storage_request(context, token, "POST", route, {}, missing=(503, f"{AREA}-storage-unavailable"))
        url = _signed_url(context, signed, "url", f"/{route}?")
    except HTTPException:
        # Without an upload URL the reservation is useless; release its quota now.
        with suppress(HTTPException):
            await _data(context, token, "delete", {"p_id": str(stored.report.id)})
        raise
    return ReportUpload(
        report=stored.report, upload_url=url, content_type=REPORT_KINDS[stored.report.kind][0],
        expires_at=stored.report.created_at + timedelta(seconds=UPLOAD_URL_SECONDS),
    )


async def complete_report(context: ServiceContext, token: str, report_id: UUID) -> Report:
    return _report(await _data(context, token, "complete", {"p_id": str(report_id)}))


async def fetch_reports(context: ServiceContext, token: str, limit: int, offset: int, project_id: UUID | None = None) -> ReportList:
    rows = await _data(context, token, "list", {"p_limit": limit + 1, "p_offset": offset, "p_project_id": str(project_id) if project_id else None})
    if not isinstance(rows, list):
        raise HTTPException(502, f"{AREA}-invalid-response")
    items = [_report(row) for row in rows[:limit]]
    return ReportList(items=items, next_offset=offset + limit if len(rows) > limit and offset + limit <= MAX_REPORT_OFFSET else None)


async def download_report(context: ServiceContext, identity: Identity, token: str, report_id: UUID) -> ReportDownload:
    stored = _stored(await _data(context, token, "object", {"p_id": str(report_id)}), identity)
    if stored.report.status != "ready":
        raise HTTPException(409, "report-not-ready")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=DOWNLOAD_URL_SECONDS)
    route = f"object/sign/{REPORT_BUCKET}/{stored.object_path}"
    signed = await storage_request(context, token, "POST", route, {"expiresIn": DOWNLOAD_URL_SECONDS}, missing=(404, "report-object-missing"))
    file_name = REPORT_KINDS[stored.report.kind][2]
    url = f"{_signed_url(context, signed, 'signedURL', f'/{route}?')}&download={quote(file_name)}"
    return ReportDownload(report=stored.report, download_url=url, file_name=file_name, expires_at=expires_at)


async def remove_report(context: ServiceContext, identity: Identity, token: str, report_id: UUID) -> None:
    """Object first: if Storage fails, the record stays listed and deletion can be retried."""
    stored = _stored(await _data(context, token, "object", {"p_id": str(report_id)}), identity)
    # Removing an absent object succeeds, so a retry after a partial failure completes.
    await storage_request(context, token, "DELETE", f"object/{REPORT_BUCKET}", {"prefixes": [stored.object_path]}, missing=(503, f"{AREA}-storage-unavailable"))
    await _data(context, token, "delete", {"p_id": str(report_id)})


router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("", response_model=ReportUpload, status_code=201)
async def create_report_upload(payload: ReportUploadRequest, request: Request, identity: CurrentIdentity, token: Token):
    return await reserve_report(context_of(request), identity, token, payload)


@router.get("", response_model=ReportList)
async def list_reports(request: Request, _identity: CurrentIdentity, token: Token, limit: int = Query(DEFAULT_REPORT_PAGE_SIZE, ge=1, le=MAX_REPORT_PAGE_SIZE), offset: int = Query(0, ge=0, le=MAX_REPORT_OFFSET), project_id: Annotated[UUID | None, Query(alias="projectId")] = None):
    return await fetch_reports(context_of(request), token, limit, offset, project_id)


@router.post("/{report_id}/complete", response_model=Report)
async def complete(report_id: UUID, request: Request, _identity: CurrentIdentity, token: Token):
    return await complete_report(context_of(request), token, report_id)


@router.post("/{report_id}/download", response_model=ReportDownload)
async def create_report_download(report_id: UUID, request: Request, identity: CurrentIdentity, token: Token):
    return await download_report(context_of(request), identity, token, report_id)


@router.delete("/{report_id}", status_code=204)
async def delete_report(report_id: UUID, request: Request, identity: CurrentIdentity, token: Token):
    await remove_report(context_of(request), identity, token, report_id)
    return Response(status_code=204)
