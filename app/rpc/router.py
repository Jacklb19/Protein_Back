"""Routes RPC paths (/package.Service/Method) to the generated applications; everything else goes to REST.

Services sit at the root path because native gRPC clients cannot use a path prefix. The same
applications answer the Connect, gRPC-Web and gRPC protocols, chosen by the request content type.
"""

from __future__ import annotations

from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from .gen.proteinforge.account.v1.account_service_connect import AccountServiceASGIApplication
from .gen.proteinforge.assistant.v1.assistant_service_connect import AssistantServiceASGIApplication
from .gen.proteinforge.project.v1.project_service_connect import ProjectServiceASGIApplication
from .gen.proteinforge.reference.v1.reference_service_connect import ReferenceServiceASGIApplication
from .services import AccountService, AssistantService, ProjectService, ReferenceService

# Largest request message accepted, in bytes: a project with 100,000 residues plus its snapshot fits.
RPC_READ_MAX_BYTES = 2_000_000


def rpc_applications(state: Any, version: str) -> dict[str, ASGIApp]:
    """Generated ASGI applications keyed by the path prefix of their service."""
    applications = (
        AccountServiceASGIApplication(AccountService(state, version), read_max_bytes=RPC_READ_MAX_BYTES),
        ProjectServiceASGIApplication(ProjectService(state, version), read_max_bytes=RPC_READ_MAX_BYTES),
        ReferenceServiceASGIApplication(ReferenceService(state, version), read_max_bytes=RPC_READ_MAX_BYTES),
        AssistantServiceASGIApplication(AssistantService(state, version), read_max_bytes=RPC_READ_MAX_BYTES),
    )
    return {f"{application.path}/": application for application in applications}


class RpcRouter:
    """ASGI middleware placed inside CORS, so preflight and headers apply to RPC as to REST."""

    def __init__(self, app: ASGIApp, applications: dict[str, ASGIApp]) -> None:
        self.app = app
        self.applications = applications

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            for prefix, application in self.applications.items():
                if path.startswith(prefix):
                    await application(scope, receive, send)
                    return
        await self.app(scope, receive, send)
