"""HTTP error handling (PRD 5.1: no stack trace ever reaches a client).

Every response follows one shape, so the frontend has a single error contract:

    {"error": {"code": "TUTOR_NOT_FOUND", "message": "...", "request_id": "..."}}

The `request_id` is the bridge between what the user sees and what is in the logs: the message
stays generic, the full exception is logged under that id.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, RateLimitError
from app.core.logging import get_logger, get_request_id

logger = get_logger(__name__)


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "error": {"code": code, "message": message, "request_id": get_request_id()}
    }
    if details:
        payload["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


async def handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)

    headers = None
    if isinstance(exc, RateLimitError):
        headers = {"Retry-After": str(exc.retry_after_seconds)}

    # Client mistakes are noise at warning level; server-side failures are not.
    log = logger.warning if exc.status_code < 500 else logger.error
    log("request_failed", error_code=exc.code, status_code=exc.status_code)

    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details or None,
        headers=headers,
    )


async def handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)

    # Field paths and messages are safe and genuinely useful; the raw input is not echoed back.
    fields = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message="Dados invalidos.",
        details={"fields": fields},
    )


async def handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)

    return error_response(
        status_code=exc.status_code,
        code=_HTTP_CODES.get(exc.status_code, "HTTP_ERROR"),
        message=str(exc.detail) if exc.detail else "Requisicao invalida.",
        headers=dict(exc.headers) if exc.headers else None,
    )


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """Last resort: log everything, disclose nothing."""
    logger.exception("unhandled_exception", error_type=type(exc).__name__)

    return error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="Erro interno. Se o problema persistir, informe o identificador da requisicao.",
    )


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
