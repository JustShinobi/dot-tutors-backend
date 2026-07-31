"""Password hashing and token issuance (PRD 3.4 and 4.1.2).

Two distinct token audiences share one signing key but never each other's scope:

* **admin** — issued after a password login, authorises the management API;
* **embed session** — issued to an anonymous widget after its embed key and `Origin` are
  validated, authorises only the chat of one session.

The `aud` claim is verified on every decode, so an embed session token can never be replayed
against the admin API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import bcrypt
import jwt
from jwt import InvalidTokenError

from app.core.config import Settings
from app.core.errors import AuthenticationError

_BCRYPT_MAX_PASSWORD_BYTES: Final = 72
"""bcrypt silently truncates beyond 72 bytes; rejecting is safer than pretending it worked."""


class TokenAudience(StrEnum):
    ADMIN = "dot-tutors:admin"
    EMBED_SESSION = "dot-tutors:embed-session"


class AdminClaims:
    """Validated payload of an admin token."""

    __slots__ = ("email", "role", "subject")

    def __init__(self, *, subject: str, email: str, role: str) -> None:
        self.subject = subject
        self.email = email
        self.role = role


class EmbedSessionClaims:
    """Validated payload of an embed session token."""

    __slots__ = ("embed_key_id", "session_id", "tutor_id")

    def __init__(self, *, session_id: str, tutor_id: str, embed_key_id: str) -> None:
        self.session_id = session_id
        self.tutor_id = tutor_id
        self.embed_key_id = embed_key_id


# --- passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_PASSWORD_BYTES:
        msg = f"A senha excede {_BCRYPT_MAX_PASSWORD_BYTES} bytes."
        raise ValueError(msg)
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification. Returns False instead of raising on a malformed hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- tokens ----------------------------------------------------------------


def _encode(
    settings: Settings,
    *,
    subject: str,
    audience: TokenAudience,
    expires_in: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, int]:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "aud": audience.value,
        "iat": now,
        "exp": now + expires_in,
        **(extra_claims or {}),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(expires_in.total_seconds())


def _decode(settings: Settings, token: str, *, audience: TokenAudience) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=audience.value,
            options={"require": ["exp", "sub", "aud"]},
        )
    except InvalidTokenError as exc:
        # The reason (expired, wrong audience, bad signature) is deliberately not echoed back.
        raise AuthenticationError from exc


def create_admin_token(
    settings: Settings, *, admin_id: str, email: str, role: str
) -> tuple[str, int]:
    return _encode(
        settings,
        subject=admin_id,
        audience=TokenAudience.ADMIN,
        expires_in=timedelta(minutes=settings.admin_access_token_ttl_minutes),
        extra_claims={"email": email, "role": role},
    )


def decode_admin_token(settings: Settings, token: str) -> AdminClaims:
    payload = _decode(settings, token, audience=TokenAudience.ADMIN)
    return AdminClaims(
        subject=str(payload["sub"]),
        email=str(payload.get("email", "")),
        role=str(payload.get("role", "")),
    )


def create_embed_session_token(
    settings: Settings, *, session_id: str, tutor_id: str, embed_key_id: str
) -> tuple[str, int]:
    return _encode(
        settings,
        subject=session_id,
        audience=TokenAudience.EMBED_SESSION,
        expires_in=timedelta(minutes=settings.embed_session_ttl_minutes),
        extra_claims={"tutor_id": tutor_id, "embed_key_id": embed_key_id},
    )


def decode_embed_session_token(settings: Settings, token: str) -> EmbedSessionClaims:
    payload = _decode(settings, token, audience=TokenAudience.EMBED_SESSION)
    try:
        return EmbedSessionClaims(
            session_id=str(payload["sub"]),
            tutor_id=str(payload["tutor_id"]),
            embed_key_id=str(payload["embed_key_id"]),
        )
    except KeyError as exc:
        raise AuthenticationError from exc
