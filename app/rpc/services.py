"""Service implementations. Each method reuses the same domain functions as the REST routes."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from ..assistant import InterpretRequest, interpret, remaining_header
from ..folding import predict
from ..folding_models import FoldRequest
from ..config import Settings
from ..context import ServiceContext, context_from_state
from ..identity import Identity, resolve_identity, token_from_header
from ..projects import DEFAULT_PAGE_SIZE, MAX_OFFSET, MAX_PAGE_SIZE, ProjectCreate, ProjectUpdate
from ..projects import fetch_project, fetch_projects, fetch_versions, remove_project, restore_version, save_project
from ..project_config import PROJECT_NAME_LENGTH
from ..report_config import DEFAULT_REPORT_PAGE_SIZE, MAX_REPORT_OFFSET, MAX_REPORT_PAGE_SIZE
from ..reports import ReportUploadRequest, complete_report, download_report, fetch_reports, remove_report, reserve_report
from ..uniprot import UNIPROT_SEARCH_SIZE, fetch_entry, search_entries
from ..residue_mapping import MAPPING_RESPONSE_MAX_BYTES, get_residue_mapping
from . import convert
from .errors import rpc_errors
from .gen.proteinforge.account.v1 import account_service_pb as account_pb
from .gen.proteinforge.assistant.v1 import assistant_service_pb as assistant_pb
from .gen.proteinforge.project.v1 import project_service_pb as project_pb
from .gen.proteinforge.reference.v1 import reference_service_pb as reference_pb
from .gen.proteinforge.report.v1 import report_service_pb as report_pb
from .gen.proteinforge.folding.v1 import folding_service_pb as folding_pb

AUTHORIZATION_HEADER = "authorization"


class StateBound:
    """Reads settings and the shared HTTP client from the running app on every call."""

    def __init__(self, state: Any, version: str) -> None:
        self._state = state
        self._version = version

    @property
    def context(self) -> ServiceContext:
        return context_from_state(self._state)

    @property
    def settings(self) -> Settings:
        return self._state.settings

    def token(self, ctx: RequestContext[Any, Any]) -> str:
        return token_from_header(ctx.request_headers.get(AUTHORIZATION_HEADER, ""))

    async def identity(self, ctx: RequestContext[Any, Any]) -> tuple[Identity, str]:
        token = self.token(ctx)
        context = self.context
        return await resolve_identity(context.settings, context.http_client, token), token


def project_id(value: str, field: str = "id") -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ConnectError(Code.INVALID_ARGUMENT, f"validation: {field}") from error


def optional_id(value: str, field: str) -> UUID | None:
    return project_id(value, field) if value else None


class AccountService(StateBound):
    @rpc_errors
    async def get_status(self, request: account_pb.GetStatusRequest, ctx: RequestContext) -> account_pb.GetStatusResponse:
        return account_pb.GetStatusResponse(status="ok", version=self._version, identity_configured=self.settings.identity_enabled)

    @rpc_errors
    async def get_me(self, request: account_pb.GetMeRequest, ctx: RequestContext) -> account_pb.GetMeResponse:
        identity, _token = await self.identity(ctx)
        return account_pb.GetMeResponse(id=identity.id, email=identity.email)


class FoldingService(StateBound):
    @rpc_errors
    async def predict(self, request: folding_pb.PredictRequest, ctx: RequestContext) -> folding_pb.PredictResponse:
        remaining = ctx.timeout_ms
        if remaining is not None and remaining <= 0:
            raise ConnectError(Code.DEADLINE_EXCEEDED, "folding-timeout")
        try:
            # The RPC context reports the remaining client budget, including
            # authentication; the provider still keeps its own 30-second bound.
            async with asyncio.timeout(None if remaining is None else remaining / 1000):
                identity, _token = await self.identity(ctx)
                body = FoldRequest(sequence=request.sequence, consent_to_send_sequence=request.consent_to_send_sequence)
                if ctx.timeout_ms is not None and ctx.timeout_ms <= 0:
                    raise ConnectError(Code.DEADLINE_EXCEEDED, "folding-timeout")
                result = await predict(self._state, body, identity.id)
                if ctx.timeout_ms is not None and ctx.timeout_ms <= 0:
                    raise ConnectError(Code.DEADLINE_EXCEEDED, "folding-timeout")
        except TimeoutError as error:
            raise ConnectError(Code.DEADLINE_EXCEEDED, "folding-timeout") from error
        return folding_pb.PredictResponse(
            schema_version=result.schema_version, sequence=result.sequence, sequence_sha256=result.sequence_sha256,
            pdb=result.pdb, pdb_sha256=result.pdb_sha256, provider=result.provider, model=result.model,
            retrieved_at=convert.timestamp(result.retrieved_at), source_url=result.source_url,
        )


class ProjectService(StateBound):
    @rpc_errors
    async def list_projects(self, request: project_pb.ListProjectsRequest, ctx: RequestContext) -> project_pb.ListProjectsResponse:
        identity, token = await self.identity(ctx)
        if not 0 <= request.page_size <= MAX_PAGE_SIZE:
            raise ConnectError(Code.INVALID_ARGUMENT, "validation: page_size")
        if len(request.query) > PROJECT_NAME_LENGTH or any(ord(char) < 32 for char in request.query):
            raise ConnectError(Code.INVALID_ARGUMENT, "validation: query")
        query = request.query.strip()
        order = convert.project_order(request.order)
        size = request.page_size or DEFAULT_PAGE_SIZE
        listing = await fetch_projects(self.context, identity.id, token, size, convert.page_offset(request.page_token, MAX_OFFSET, query, order), query, order)
        return project_pb.ListProjectsResponse(
            projects=[convert.summary_message(item) for item in listing.items],
            next_page_token=convert.page_token(listing.next_offset, query, order),
            query_applied=True,
        )

    @rpc_errors
    async def get_project(self, request: project_pb.GetProjectRequest, ctx: RequestContext) -> project_pb.GetProjectResponse:
        _identity, token = await self.identity(ctx)
        project = await fetch_project(self.context, token, project_id(request.id))
        return project_pb.GetProjectResponse(project=convert.project_message(project))

    @rpc_errors
    async def create_project(self, request: project_pb.CreateProjectRequest, ctx: RequestContext) -> project_pb.CreateProjectResponse:
        identity, token = await self.identity(ctx)
        document = convert.write_document(request.name, request.active_sequence, request.workspace)
        payload = ProjectCreate.model_validate({**document, "requestId": optional_id(request.request_id, "request_id")})
        project = await save_project(self.context, token, payload, owner_id=identity.id)
        return project_pb.CreateProjectResponse(project=convert.project_message(project))

    @rpc_errors
    async def update_project(self, request: project_pb.UpdateProjectRequest, ctx: RequestContext) -> project_pb.UpdateProjectResponse:
        identity, token = await self.identity(ctx)
        expected = convert.moment(request.expected_update_time, "expected_update_time")
        document = convert.write_document(request.name, request.active_sequence, request.workspace)
        payload = ProjectUpdate.model_validate({**document, "expectedUpdatedAt": expected})
        project = await save_project(self.context, token, payload, project_id(request.id), expected, identity.id)
        return project_pb.UpdateProjectResponse(project=convert.project_message(project))

    @rpc_errors
    async def delete_project(self, request: project_pb.DeleteProjectRequest, ctx: RequestContext) -> project_pb.DeleteProjectResponse:
        identity, token = await self.identity(ctx)
        await remove_project(self.context, token, project_id(request.id), convert.moment(request.expected_update_time, "expected_update_time"), identity.id)
        return project_pb.DeleteProjectResponse()


    @rpc_errors
    async def list_project_versions(self, request: project_pb.ListProjectVersionsRequest, ctx: RequestContext) -> project_pb.ListProjectVersionsResponse:
        _identity, token = await self.identity(ctx)
        listing = await fetch_versions(self.context, token, project_id(request.project_id, "project_id"))
        return project_pb.ListProjectVersionsResponse(versions=[convert.version_message(item) for item in listing.items])

    @rpc_errors
    async def restore_project_version(self, request: project_pb.RestoreProjectVersionRequest, ctx: RequestContext) -> project_pb.RestoreProjectVersionResponse:
        identity, token = await self.identity(ctx)
        expected = convert.moment(request.expected_update_time, "expected_update_time")
        project = await restore_version(self.context, token, project_id(request.project_id, "project_id"), project_id(request.version_id, "version_id"), expected, identity.id)
        return project_pb.RestoreProjectVersionResponse(project=convert.project_message(project))


class ReportService(StateBound):
    @rpc_errors
    async def create_report_upload(self, request: report_pb.CreateReportUploadRequest, ctx: RequestContext) -> report_pb.CreateReportUploadResponse:
        identity, token = await self.identity(ctx)
        payload = ReportUploadRequest(project_id=optional_id(request.project_id, "project_id"), kind=convert.report_kind(request.kind), size_bytes=request.size_bytes, sha256=request.sha256)
        upload = await reserve_report(self.context, identity, token, payload)
        return report_pb.CreateReportUploadResponse(
            report=convert.report_message(upload.report), upload_url=upload.upload_url,
            content_type=upload.content_type, expire_time=convert.timestamp(upload.expires_at),
        )

    @rpc_errors
    async def complete_report(self, request: report_pb.CompleteReportRequest, ctx: RequestContext) -> report_pb.CompleteReportResponse:
        _identity, token = await self.identity(ctx)
        return report_pb.CompleteReportResponse(report=convert.report_message(await complete_report(self.context, token, project_id(request.id))))

    @rpc_errors
    async def list_reports(self, request: report_pb.ListReportsRequest, ctx: RequestContext) -> report_pb.ListReportsResponse:
        _identity, token = await self.identity(ctx)
        if not 0 <= request.page_size <= MAX_REPORT_PAGE_SIZE:
            raise ConnectError(Code.INVALID_ARGUMENT, "validation: page_size")
        project = optional_id(request.project_id, "project_id")
        # The token is bound to the project filter, and never valid for project listings.
        scope = f"reports:{project or ''}"
        offset = convert.page_offset(request.page_token, MAX_REPORT_OFFSET, scope)
        listing = await fetch_reports(self.context, token, request.page_size or DEFAULT_REPORT_PAGE_SIZE, offset, project)
        return report_pb.ListReportsResponse(
            reports=[convert.report_message(item) for item in listing.items],
            next_page_token=convert.page_token(listing.next_offset, scope),
        )

    @rpc_errors
    async def create_report_download(self, request: report_pb.CreateReportDownloadRequest, ctx: RequestContext) -> report_pb.CreateReportDownloadResponse:
        identity, token = await self.identity(ctx)
        download = await download_report(self.context, identity, token, project_id(request.id))
        return report_pb.CreateReportDownloadResponse(
            report=convert.report_message(download.report), download_url=download.download_url,
            file_name=download.file_name, expire_time=convert.timestamp(download.expires_at),
        )

    @rpc_errors
    async def delete_report(self, request: report_pb.DeleteReportRequest, ctx: RequestContext) -> report_pb.DeleteReportResponse:
        identity, token = await self.identity(ctx)
        await remove_report(self.context, identity, token, project_id(request.id))
        return report_pb.DeleteReportResponse()


class ReferenceService(StateBound):
    @rpc_errors
    async def search_entries(self, request: reference_pb.SearchEntriesRequest, ctx: RequestContext) -> reference_pb.SearchEntriesResponse:
        identity, _token = await self.identity(ctx)
        result = await search_entries(self.context, request.query, identity.id)
        return reference_pb.SearchEntriesResponse(
            entries=[convert.entry_summary_message(item) for item in result.items],
            limit=UNIPROT_SEARCH_SIZE,
            truncated=result.truncated,
            retrieve_time=convert.timestamp(result.retrieved_at),
        )

    @rpc_errors
    async def get_entry(self, request: reference_pb.GetEntryRequest, ctx: RequestContext) -> reference_pb.GetEntryResponse:
        identity, _token = await self.identity(ctx)
        return reference_pb.GetEntryResponse(entry=convert.entry_message(await fetch_entry(self.context, request.accession, identity.id)))

    @rpc_errors
    async def get_residue_mapping(self, request: reference_pb.GetResidueMappingRequest, ctx: RequestContext) -> reference_pb.GetResidueMappingResponse:
        identity, _token = await self.identity(ctx)
        result = await get_residue_mapping(self.context, request.pdb_id, request.accession, request.sequence_version, identity.id)
        message = convert.residue_mapping_message(result)
        # JSON/REST and binary are checked separately, including their full envelope.
        if len(message.to_binary()) > MAPPING_RESPONSE_MAX_BYTES or len(message.to_json().encode('utf-8')) > MAPPING_RESPONSE_MAX_BYTES:
            raise ConnectError(Code.RESOURCE_EXHAUSTED, 'sifts-response-too-large')
        return message


class AssistantService(StateBound):
    @rpc_errors
    async def interpret(self, request: assistant_pb.InterpretRequest, ctx: RequestContext) -> assistant_pb.InterpretResponse:
        identity, _token = await self.identity(ctx)
        document = InterpretRequest.model_validate(convert.interpret_document(request))
        context = self.context
        interpretation = await interpret(context, document, identity.id)
        for name, value in remaining_header(context, identity.id).items():
            ctx.response_headers[name] = value
        return convert.interpretation_message(interpretation)
