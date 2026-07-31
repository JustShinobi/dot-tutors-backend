"""FastAPI dependencies.

Everything the routes need is assembled here, so route functions stay thin and every
collaborator is trivially overridable in tests via `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.db.models.admin import AdminUser
from app.repositories.embed import EmbedKeyRepository
from app.repositories.tutor import SourceRepository, TutorRepository
from app.services.auth_service import AuthService
from app.services.embed_service import EmbedService
from app.services.tutor_service import TutorService

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
