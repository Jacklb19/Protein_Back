"""Domain failures expressed as RPC status codes. Messages carry stable codes, never user data."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from pydantic import ValidationError
from starlette.exceptions import HTTPException

# HTTP statuses raised by the shared domain functions, as gRPC codes.
STATUS_CODES: dict[int, Code] = {
    400: Code.INVALID_ARGUMENT,
    401: Code.UNAUTHENTICATED,
    403: Code.PERMISSION_DENIED,
    404: Code.NOT_FOUND,
    409: Code.ABORTED,
    422: Code.INVALID_ARGUMENT,
    429: Code.RESOURCE_EXHAUSTED,
    413: Code.RESOURCE_EXHAUSTED,
    502: Code.UNAVAILABLE,
    503: Code.UNAVAILABLE,
    504: Code.DEADLINE_EXCEEDED,
}

# Failures that mean "this server is not set up for it" rather than "try again".
DETAIL_CODES: dict[str, Code] = {
    "projects-schema-unavailable": Code.FAILED_PRECONDITION,
    "identity-not-configured": Code.FAILED_PRECONDITION,
    "assistant-not-configured": Code.FAILED_PRECONDITION,
    "sifts-reference-version": Code.FAILED_PRECONDITION,
    "sifts-reference-mismatch": Code.FAILED_PRECONDITION,
}

logger = logging.getLogger(__name__)

VALIDATION_CODE = "validation"
INTERNAL_CODE = "internal"

P = ParamSpec("P")
R = TypeVar("R")


def to_connect_error(error: Exception) -> ConnectError:
    if isinstance(error, ConnectError):
        return error
    if isinstance(error, HTTPException):
        detail = error.detail if isinstance(error.detail, str) else "http-error"
        if error.status_code >= 500:
            logger.warning("RPC method failed: %s %s", error.status_code, detail)
        return ConnectError(DETAIL_CODES.get(detail, STATUS_CODES.get(error.status_code, Code.INTERNAL)), detail)
    if isinstance(error, ValidationError):
        # Field paths only: submitted values (sequences, names) never appear in errors.
        fields = sorted({".".join(str(part) for part in item.get("loc", ())) for item in error.errors()})
        return ConnectError(Code.INVALID_ARGUMENT, f"{VALIDATION_CODE}: {', '.join(fields)}")
    logger.exception("Unexpected failure in an RPC method", exc_info=error)
    return ConnectError(Code.INTERNAL, INTERNAL_CODE)


def rpc_errors(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Runs a service method and reports any failure with its RPC code."""

    @wraps(function)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await function(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - every failure must leave as a typed RPC error
            raise to_connect_error(error) from error

    return wrapper
