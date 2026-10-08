"""RFC 9457 problem details with a correlation identifier and no sensitive payloads."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import REQUEST_ID_HEADER

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://proteinforge.invalid/problems/"

TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Unprocessable content",
    429: "Too many requests",
    500: "Internal server error",
    502: "Bad gateway",
    503: "Service unavailable",
    504: "Gateway timeout",
}


def problem(request: Request, status: int, detail: str, code: str) -> JSONResponse:
    """Problem document; the request identifier lets logs be correlated without exposing data."""
    request_id = getattr(request.state, "request_id", "")
    body = {
        "type": f"{PROBLEM_TYPE_BASE}{code}",
        "title": TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "requestId": request_id,
    }
    headers = {REQUEST_ID_HEADER: request_id} if request_id else {}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE, headers=headers)


async def http_exception_handler(request: Request, exception: StarletteHTTPException) -> JSONResponse:
    code = exception.detail if isinstance(exception.detail, str) else "http-error"
    response = problem(request, exception.status_code, str(exception.detail), code.replace(" ", "-").lower())
    for key, value in (exception.headers or {}).items():
        response.headers[key] = value
    return response


async def validation_exception_handler(request: Request, exception: RequestValidationError) -> JSONResponse:
    # Field names are reported, never the submitted values, so sequences and secrets stay out of responses.
    fields = sorted({".".join(str(part) for part in error.get("loc", ())) for error in exception.errors()})
    return problem(request, 422, f"Invalid request: {', '.join(fields)}", "validation")


async def unexpected_exception_handler(request: Request, _exception: Exception) -> JSONResponse:
    return problem(request, 500, "The server could not complete the request.", "internal")
