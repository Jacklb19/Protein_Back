"""Bearer token validation against Supabase Auth; the server never sees passwords."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status

from .config import Settings

BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class Identity:
    """The authenticated principal; `id` matches `profiles.id` in the database."""

    id: str
    email: str


def bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith(BEARER_PREFIX) or not header[len(BEARER_PREFIX):].strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing-token", headers={"WWW-Authenticate": "Bearer"})
    return header[len(BEARER_PREFIX):].strip()


async def resolve_identity(settings: Settings, client: httpx.AsyncClient, token: str) -> Identity:
    """Asks Supabase who the token belongs to. Network or upstream failures become explicit 502/504 errors."""
    if not settings.identity_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "identity-not-configured")
    headers = {"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"}
    try:
        response = await client.get(settings.identity_user_url, headers=headers, timeout=settings.identity_timeout_seconds)
    except httpx.TimeoutException as error:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "identity-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "identity-unreachable") from error
    if response.status_code in (401, 403):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid-token", headers={"WWW-Authenticate": "Bearer"})
    if response.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "identity-error")
    try:
        payload = response.json()
    except ValueError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "identity-error") from error
    user_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "identity-error")
    email = payload.get("email")
    return Identity(id=user_id, email=email if isinstance(email, str) else "")


async def current_identity(request: Request, token: Annotated[str, Depends(bearer_token)]) -> Identity:
    settings: Settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.http_client
    return await resolve_identity(settings, client, token)


CurrentIdentity = Annotated[Identity, Depends(current_identity)]
