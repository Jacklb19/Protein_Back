"""What the domain functions need from the running app, independent of REST or RPC."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

from .config import Settings


@dataclass(frozen=True)
class ServiceContext:
    settings: Settings
    http_client: httpx.AsyncClient


def context_from_state(state: Any) -> ServiceContext:
    return ServiceContext(settings=state.settings, http_client=state.http_client)


def context_of(request: Request) -> ServiceContext:
    return context_from_state(request.app.state)
