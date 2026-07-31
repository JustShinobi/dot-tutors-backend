"""Administrator authentication (PRD 4.1.2)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    AdminClaims,
    create_admin_token,
    decode_admin_token,
    hash_password,
    verify_password,
)
from app.db.models.admin import AdminRole, AdminUser

logger = get_logger(__name__)

_DUMMY_HASH = hash_password("dummy-password-for-timing-equalisation")


class AuthService:
    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def authenticate(self, email: str, password: str) -> tuple[str, int]:
        """Validate credentials and return `(access_token, expires_in_seconds)`.

        A missing user and a wrong password produce the same error *and* roughly the same
        latency: the hash comparison runs against a dummy hash when the user does not exist, so
        response time cannot be used to enumerate valid e-mails.
        """
        user = await self._get_by_email(email)
        password_hash = user.password_hash if user is not None else _DUMMY_HASH
        password_matches = verify_password(password, password_hash)

        if user is None or not password_matches or not user.is_active:
            logger.warning("admin_login_failed", email_domain=_domain_of(email))
            raise AuthenticationError("E-mail ou senha invalidos.")

        logger.info("admin_login_succeeded", admin_id=user.id)
        return create_admin_token(
            self._settings, admin_id=user.id, email=user.email, role=str(user.role)
        )

    async def resolve_admin(self, token: str) -> AdminUser:
        """Turn a bearer token into the current admin, rejecting stale or disabled accounts."""
        claims: AdminClaims = decode_admin_token(self._settings, token)

        user = await self._session.get(AdminUser, claims.subject)
        if user is None or not user.is_active or user.role is not AdminRole.ADMIN:
            raise AuthenticationError
        return user

    async def _get_by_email(self, email: str) -> AdminUser | None:
        result = await self._session.execute(
            select(AdminUser).where(AdminUser.email == email.strip().lower())
        )
        return result.scalar_one_or_none()


def _domain_of(email: str) -> str:
    """Log only the domain: enough to spot an attack pattern, without storing the identity."""
    _, _, domain = email.partition("@")
    return domain or "unknown"
