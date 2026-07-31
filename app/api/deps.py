"""FastAPI dependencies.

Everything the routes need is assembled here, so route functions stay thin and every
collaborator is trivially overridable in tests via `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.contracts import AgentRunner
from app.api.client_ip import client_ip
from app.core.config import Settings
from app.core.errors import AgentUnavailableError, AuthenticationError, SessionExpiredError
from app.core.logging import get_logger
from app.core.rate_limit import TokenBucketLimiter
from app.core.security import decode_embed_session_token
from app.db.models.admin import AdminUser
from app.db.models.chat import ChatSession
from app.repositories.chat import ChatRepository
from app.repositories.embed import EmbedKeyRepository
from app.repositories.tutor import SourceRepository, TutorRepository
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.embed_service import EmbedService
from app.services.source_service import SourceService
from app.services.tutor_service import TutorService

logger = get_logger(__name__)

# `auto_error=False` so a missing header raises our own AuthenticationError, keeping the error
# body identical to every other 401 instead of FastAPI's default shape.
_bearer_scheme = HTTPBearer(auto_error=False, description="Token JWT de administrador")


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request, committed by the route on success."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session=session, settings=settings)


def get_tutor_service(session: SessionDep, settings: SettingsDep) -> TutorService:
    return TutorService(
        tutors=TutorRepository(session),
        sources=SourceRepository(session),
        settings=settings,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
TutorServiceDep = Annotated[TutorService, Depends(get_tutor_service)]


def get_embed_service(
    session: SessionDep, settings: SettingsDep, tutors: TutorServiceDep
) -> EmbedService:
    return EmbedService(
        keys=EmbedKeyRepository(session),
        tutors=tutors,
        settings=settings,
    )


EmbedServiceDep = Annotated[EmbedService, Depends(get_embed_service)]


async def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    auth_service: AuthServiceDep,
) -> AdminUser:
    """Guard for the management API (PRD 4.1.2)."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Token de administrador ausente.")
    return await auth_service.resolve_admin(credentials.credentials)


CurrentAdmin = Annotated[AdminUser, Depends(require_admin)]


def get_request_origin(request: Request) -> str | None:
    """The `Origin` header, which is what actually authorises an embed (PRD 3.4)."""
    return request.headers.get("origin")


OriginDep = Annotated[str | None, Depends(get_request_origin)]


# --- embed runtime ---------------------------------------------------------


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The shared outbound HTTP client, created once in the application lifespan."""
    client: httpx.AsyncClient = request.app.state.http_client
    return client


def get_source_service(
    session: SessionDep,
    settings: SettingsDep,
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> SourceService:
    return SourceService(session=session, settings=settings, http_client=http_client)


def get_agent_runner(request: Request) -> AgentRunner:
    """The agent runner built once at startup (building it creates the model client).

    Startup deliberately does not crash when the model is unconfigured, so the admin API stays
    usable — but that leaves `agent_runner` as `None`, and a chat request must then fail *here*,
    in a dependency, with a real status code. Letting `None` reach the service produced an
    `AttributeError` halfway through an already-open SSE response, which the client could only
    observe as a truncated stream.
    """
    runner: AgentRunner | None = request.app.state.agent_runner
    if runner is None:
        reason = getattr(request.app.state, "agent_runner_error", "runner nao inicializado")
        logger.error("agent_runner_missing", reason=reason)
        raise AgentUnavailableError
    return runner


def get_chat_service(
    session: SessionDep,
    settings: SettingsDep,
    embeds: EmbedServiceDep,
    sources: Annotated[SourceService, Depends(get_source_service)],
    runner: Annotated[AgentRunner, Depends(get_agent_runner)],
) -> ChatService:
    return ChatService(
        chats=ChatRepository(session),
        embeds=embeds,
        sources=sources,
        runner=runner,
        settings=settings,
    )


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


async def require_embed_session(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    service: ChatServiceDep,
    settings: SettingsDep,
) -> ChatSession:
    """Resolve the session token issued by `POST /embed/session`.

    The `aud` claim keeps this token strictly separate from an administrator one.
    """
    if credentials is None or not credentials.credentials:
        raise SessionExpiredError("Sessao ausente. Recarregue o widget.")

    claims = decode_embed_session_token(settings, credentials.credentials)
    return await service.resolve_session(claims.session_id)


CurrentEmbedSession = Annotated[ChatSession, Depends(require_embed_session)]


# --- rate limiting ---------------------------------------------------------
#
# Held at module level so the buckets survive between requests. They are per-process, which is
# the documented limitation of the in-memory approach (see `app/core/rate_limit.py`).

_limiters: dict[str, TokenBucketLimiter] = {}


def _limiter(name: str, capacity: int) -> TokenBucketLimiter:
    """Fetch (or rebuild) a named bucket set.

    Rebuilding when the capacity changes is what lets a test tighten a limit and see it take
    effect without reaching into module state.
    """
    existing = _limiters.get(name)
    if existing is None or existing.capacity != capacity:
        existing = TokenBucketLimiter(capacity=capacity)
        _limiters[name] = existing
    return existing


def embed_rate_limiter(settings: Settings) -> TokenBucketLimiter:
    """Per conversation session."""
    return _limiter("chat_session", settings.rate_limit_chat_per_minute)


def embed_ip_rate_limiter(settings: Settings) -> TokenBucketLimiter:
    """Per (embed key, IP).

    The per-session bucket alone caps one conversation; nothing stops a script from opening a
    new session for each message. This is the ceiling that actually bounds the LLM spend an
    integrator's page can trigger.
    """
    return _limiter("chat_ip", settings.rate_limit_chat_per_ip_per_minute)


def session_rate_limiter(settings: Settings) -> TokenBucketLimiter:
    """Per IP, on session creation."""
    return _limiter("session_open", settings.rate_limit_session_per_minute)


def login_rate_limiter(settings: Settings) -> TokenBucketLimiter:
    """Per IP, on the admin login — the only endpoint that verifies a password."""
    return _limiter("login", settings.rate_limit_login_per_minute)


def prune_rate_limiters(*, older_than_seconds: float = 3_600) -> int:
    """Drop idle buckets across every limiter.

    Each distinct key allocates an entry and the keys include client IPs, so without this a
    long-running process facing many clients leaks memory slowly.
    """
    return sum(
        limiter.prune(older_than_seconds=older_than_seconds) for limiter in _limiters.values()
    )


def reset_rate_limiters() -> None:
    """Clear limiter state. Used by tests so one case cannot exhaust another's budget."""
    _limiters.clear()


def get_client_ip(request: Request) -> str:
    """The caller's address, honouring `X-Forwarded-For` only when a proxy is configured."""
    settings: Settings = request.app.state.settings
    return client_ip(request, trusted_proxy_hops=settings.trusted_proxy_hops)


ClientIpDep = Annotated[str, Depends(get_client_ip)]
