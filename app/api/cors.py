"""CORS with two policies in one middleware (PRD 5.1: "CORS coerente com o cenario de iframe").

The two surfaces of this API have genuinely different needs:

* **admin API** — called from one known origin, with credentials. A strict allowlist.
* **embed API** — called from *any* integrator's site, from inside an iframe. The set of valid
  origins is per embed key and is stored in the database, so a static list cannot express it,
  and a CORS preflight carries no key to look one up with.

The resolution rests on what CORS actually is: **a browser read-protection, not an
authorization mechanism**. A blocked CORS response has still reached the server and still ran.
Refusing unknown origins at the CORS layer would therefore protect nothing, while breaking every
legitimate integrator.

So the embed API echoes the requesting origin, and authorisation is enforced where it belongs:
`EmbedService.authorize` compares the `Origin` header against the key's allowlist and answers
403 before any work happens. Credentials are disabled — the session token travels in the
`Authorization` header, never in a cookie — so echoing the origin grants no ambient authority.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.middleware import REQUEST_ID_HEADER

EMBED_PATH_PREFIX = "/api/v1/embed"

_ADMIN_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
_EMBED_METHODS = "GET, POST, OPTIONS"
_ALLOWED_HEADERS = f"Authorization, Content-Type, {REQUEST_ID_HEADER}"
_MAX_AGE = "600"


class DualPolicyCORSMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Callable[..., Awaitable[None]], *, admin_origins: list[str]) -> None:
        super().__init__(app)
        self._admin_origins = frozenset(admin_origins)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")

        if request.method == "OPTIONS" and "access-control-request-method" in request.headers:
            return self._preflight(request, origin)

        response = await call_next(request)
        self._apply(response.headers, request=request, origin=origin)
        return response

    # --- policy ------------------------------------------------------------

    def _is_embed(self, request: Request) -> bool:
        return request.url.path.startswith(EMBED_PATH_PREFIX)

    def _preflight(self, request: Request, origin: str | None) -> Response:
        response = Response(status_code=204)
        self._apply(response.headers, request=request, origin=origin, preflight=True)
        return response

    def _apply(
        self,
        headers: MutableHeaders | Headers,
        *,
        request: Request,
        origin: str | None,
        preflight: bool = False,
    ) -> None:
        assert isinstance(headers, MutableHeaders)

        # Responses differ by origin, so caches must key on it.
        headers.append("Vary", "Origin")

        if origin is None:
            return

        if self._is_embed(request):
            headers["Access-Control-Allow-Origin"] = origin
            allowed_methods = _EMBED_METHODS
        elif origin in self._admin_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
            allowed_methods = _ADMIN_METHODS
        else:
            # Unknown origin on the admin API: emit nothing and let the browser block the read.
            return

        headers["Access-Control-Expose-Headers"] = REQUEST_ID_HEADER

        if preflight:
            headers["Access-Control-Allow-Methods"] = allowed_methods
            headers["Access-Control-Allow-Headers"] = _ALLOWED_HEADERS
            headers["Access-Control-Max-Age"] = _MAX_AGE
