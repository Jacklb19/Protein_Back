"""Service implementations. Each method reuses the same domain functions as the REST routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from ..config import Settings
from ..context import ServiceContext, context_from_state
from ..identity import Identity, resolve_identity, token_from_header
from ..projects import DEFAULT_PAGE_SIZE, MAX_OFFSET, MAX_PAGE_SIZE, ProjectUpdate, ProjectWrite
from ..projects import fetch_project, fetch_projects, remove_project, save_project
from ..project_config import PROJECT_NAME_LENGTH
from ..uniprot import UNIPROT_SEARCH_SIZE, fetch_entry, search_entries
from . import convert
from .errors import rpc_errors
from .gen.proteinforge.account.v1 import account_service_pb as account_pb
from .gen.proteinforge.project.v1 import project_service_pb as project_pb
from .gen.proteinforge.reference.v1 import reference_service_pb as reference_pb

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


def project_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ConnectError(Code.INVALID_ARGUMENT, "validation: id") from error


class AccountService(StateBound):
    @rpc_errors
    async def get_status(self, request: account_pb.GetStatusRequest, ctx: RequestContext) -> account_pb.GetStatusResponse:
        return account_pb.GetStatusResponse(status="ok", version=self._version, identity_configured=self.settings.identity_enabled)

    @rpc_errors
    async def get_me(self, request: account_pb.GetMeRequest, ctx: RequestContext) -> account_pb.GetMeResponse:
        identity, _token = await self.identity(ctx)
        return account_pb.GetMeResponse(id=identity.id, email=identity.email)


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
        _identity, token = await self.identity(ctx)
        payload = ProjectWrite.model_validate(convert.write_document(request.name, request.active_sequence, request.workspace))
        project = await save_project(self.context, token, payload)
        return project_pb.CreateProjectResponse(project=convert.project_message(project))

    @rpc_errors
    async def update_project(self, request: project_pb.UpdateProjectRequest, ctx: RequestContext) -> project_pb.UpdateProjectResponse:
        _identity, token = await self.identity(ctx)
        expected = convert.moment(request.expected_update_time, "expected_update_time")
        document = convert.write_document(request.name, request.active_sequence, request.workspace)
        payload = ProjectUpdate.model_validate({**document, "expectedUpdatedAt": expected})
        project = await save_project(self.context, token, payload, project_id(request.id), expected)
        return project_pb.UpdateProjectResponse(project=convert.project_message(project))

    @rpc_errors
    async def delete_project(self, request: project_pb.DeleteProjectRequest, ctx: RequestContext) -> project_pb.DeleteProjectResponse:
        _identity, token = await self.identity(ctx)
        await remove_project(self.context, token, project_id(request.id), convert.moment(request.expected_update_time, "expected_update_time"))
        return project_pb.DeleteProjectResponse()


class ReferenceService(StateBound):
    @rpc_errors
    async def search_entries(self, request: reference_pb.SearchEntriesRequest, ctx: RequestContext) -> reference_pb.SearchEntriesResponse:
        await self.identity(ctx)
        result = await search_entries(self.context, request.query)
        return reference_pb.SearchEntriesResponse(
            entries=[convert.entry_summary_message(item) for item in result.items],
            limit=UNIPROT_SEARCH_SIZE,
            truncated=result.truncated,
            retrieve_time=convert.timestamp(result.retrieved_at),
        )

    @rpc_errors
    async def get_entry(self, request: reference_pb.GetEntryRequest, ctx: RequestContext) -> reference_pb.GetEntryResponse:
        await self.identity(ctx)
        return reference_pb.GetEntryResponse(entry=convert.entry_message(await fetch_entry(self.context, request.accession)))
