"""Request correlation as a plain ASGI middleware.

A pure ASGI middleware keeps response trailers and streamed bodies intact, which native gRPC and
server streaming need; Starlette's `@app.middleware("http")` rebuilds the response and drops them.
"""

from __future__ import annotations

import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import REQUEST_ID_HEADER

MAX_REQUEST_ID_LENGTH = 64
NO_STORE = "no-store"


def request_id_from(scope: Scope) -> str:
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "").strip()
    return incoming if 0 < len(incoming) <= MAX_REQUEST_ID_LENGTH else uuid.uuid4().hex


class Correlation:
    """Gives every request an id (echoed back) and marks every response as not cacheable."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_from(scope)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers["Cache-Control"] = NO_STORE
            await send(message)

        await self.app(scope, receive, send_with_headers)
