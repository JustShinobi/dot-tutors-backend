"""Administrator authentication (PRD 4.1.2)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.api.deps import reset_rate_limiters
from app.core.config import Settings
from app.core.security import TokenAudience, _encode, create_embed_session_token
from app.db.models.admin import AdminUser
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


async def test_login_returns_a_bearer_token(client: AsyncClient, admin_user: AdminUser) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"]


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (ADMIN_EMAIL, "senha-errada"),
        ("naoexiste@example.com", ADMIN_PASSWORD),
    ],
)
async def test_login_failures_are_indistinguishable(
    client: AsyncClient, admin_user: AdminUser, email: str, password: str
) -> None:
    """A wrong password and an unknown e-mail must return the exact same response.

    Any difference would let an attacker enumerate valid administrator e-mails.
    """
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "E-mail ou senha invalidos."


async def test_login_rejects_a_malformed_email(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": "nao-e-email", "password": "x"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_me_returns_the_authenticated_admin(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == ADMIN_EMAIL
    assert response.json()["role"] == "admin"


async def test_me_without_a_token_is_unauthenticated(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_a_garbage_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nao-e-um-jwt"})

    assert response.status_code == 401


async def test_an_expired_token_is_rejected(
    client: AsyncClient, admin_user: AdminUser, settings: Settings
) -> None:
    expired, _ = _encode(
        settings,
        subject=admin_user.id,
        audience=TokenAudience.ADMIN,
        expires_in=timedelta(seconds=-1),
        extra_claims={"email": admin_user.email, "role": "admin"},
    )

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


async def test_an_embed_session_token_cannot_reach_the_admin_api(
    client: AsyncClient, admin_user: AdminUser, settings: Settings
) -> None:
    """The `aud` claim is what stops a widget token from being replayed as an admin token."""
    embed_token, _ = create_embed_session_token(
        settings, session_id="s-1", tutor_id="t-1", embed_key_id="k-1"
    )

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {embed_token}"}
    )

    assert response.status_code == 401


async def test_a_deactivated_admin_loses_access_immediately(
    client: AsyncClient, auth_headers: dict[str, str], admin_user: AdminUser, session: object
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    admin_user.is_active = False
    await session.commit()

    response = await client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 401


async def test_error_responses_never_leak_internals(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    body = response.json()

    assert set(body) == {"error"}
    assert set(body["error"]) <= {"code", "message", "request_id", "details"}
    assert "Traceback" not in response.text
    assert response.headers["X-Request-ID"]


# --- brute force -----------------------------------------------------------


async def test_login_attempts_are_rate_limited(
    client: AsyncClient, admin_user: AdminUser, settings: Settings
) -> None:
    """The only endpoint that verifies a password must not accept unbounded attempts."""
    settings.rate_limit_login_per_minute = 3
    reset_rate_limiters()

    statuses = [
        (
            await client.post(
                "/api/v1/auth/login",
                json={"email": ADMIN_EMAIL, "password": "senha-errada"},
            )
        ).status_code
        for _ in range(4)
    ]

    assert statuses[:3] == [401, 401, 401]
    assert statuses[3] == 429


async def test_the_login_limit_also_blocks_the_correct_password(
    client: AsyncClient, admin_user: AdminUser, settings: Settings
) -> None:
    """The check runs before the password is verified, so guessing right does not escape it.

    It also protects the CPU: bcrypt is deliberately slow, which makes unbounded login attempts
    an exhaustion vector on their own.
    """
    settings.rate_limit_login_per_minute = 2
    reset_rate_limiters()

    for _ in range(2):
        await client.post(
            "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "senha-errada"}
        )

    blocked = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["Retry-After"]) > 0
