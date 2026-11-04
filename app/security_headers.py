"""Hardening headers on every response, as a plain ASGI middleware.

Pure ASGI for the reason given in `correlation.py`: gRPC trailers and streamed bodies stay intact. Starlette
answers unhandled exceptions outside every user middleware, so `problems.py` applies the same headers itself.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Always set: the API serves data, never documents a browser should sniff or a page that should leak its URL.
SECURITY_HEADERS = (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"))
# Set only when the response has not chosen its own value.
DEFAULT_HEADERS = (("Cache-Control", "no-store"),)


def apply_security_headers(headers: MutableHeaders) -> None:
    for name, value in SECURITY_HEADERS:
        headers[name] = value
    for name, value in DEFAULT_HEADERS:
        headers.setdefault(name, value)


class SecurityHeaders:
    """Adds the hardening headers to REST, RPC, CORS and error responses alike."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                apply_security_headers(MutableHeaders(scope=message))
            await send(message)

        await self.app(scope, receive, send_with_headers)
