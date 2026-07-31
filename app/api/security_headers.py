"""Response hardening and request size limits (PRD 5.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.error_handlers import error_response
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_REQUEST_BYTES = 256 * 1024
"""Generous for a chat message or a tutor configuration, far below anything that hurts."""


class PayloadTooLargeError(AppError):
    code = "PAYLOAD_TOO_LARGE"
    status_code = 413
    message = "Requisicao grande demais."


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds the headers that are cheap, universal and easy to forget.

    This is an API, not a site: it renders no HTML, so there is no CSP to write here (the
    widget page gets its own, from the frontend). What is left still matters — MIME sniffing
    turns a JSON error into a scripting vector on old browsers, and a leaked referrer would
    carry ids to third-party sources the agent fetches.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # The API itself must never be framed. The *widget* is meant to be, but it is served by
        # the frontend, not from here.
        response.headers.setdefault("X-Frame-Options", "DENY")

        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized bodies before they are parsed.

    Enforced from the declared `Content-Length`, which every JSON client sends. A body streamed
    with chunked transfer encoding carries no such header and is **not** covered here; the
    remaining defence for that case is the ASGI server's own limit (uvicorn's
    `--limit-max-request-size`), and the schema-level `max_length` on each field. Stating the gap
    is more useful than a check that pretends to close it.
    """

    def __init__(
        self, app: Callable[..., Awaitable[None]], *, max_bytes: int = MAX_REQUEST_BYTES
    ) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            logger.warning("request_too_large", declared_bytes=int(declared))
            # Returned, not raised: this middleware sits *outside* the exception-handler stack,
            # so an AppError raised here would escape the handler that formats error bodies and
            # surface as a bare 500. Building the response keeps one error contract.
            error = PayloadTooLargeError()
            return error_response(
                status_code=error.status_code, code=error.code, message=error.message
            )

        return await call_next(request)
