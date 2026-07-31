"""Non-functional guarantees (PRD 5.1 and 5.2)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security_headers import MAX_REQUEST_BYTES
from app.db.base import utcnow
from app.db.models.chat import ChatMessage, ChatSession, MessageRole
from app.db.models.embed import EmbedKey
from app.db.models.tutor import Tutor
from app.repositories.chat import ChatRepository

# --- error disclosure ------------------------------------------------------


async def test_an_unhandled_exception_never_leaks_internals(app: FastAPI) -> None:
    """The last-resort handler is the one that matters: it runs when nothing else caught."""

    @app.get("/api/v1/_boom")
    async def boom() -> None:
        secret = "SENHA_DO_BANCO=super-secreta"
        raise RuntimeError(f"falha interna com {secret}")

    # Starlette re-raises server errors after building the response so the process can log them.
    # A real client only ever sees the response, which is what this test is about.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/_boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "SENHA_DO_BANCO" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    # The bridge between the opaque message and the full exception in the logs.
    assert response.json()["error"]["request_id"]


async def test_the_request_id_is_echoed_and_honoured(client: AsyncClient) -> None:
    """An inbound id must survive so a trace started at a proxy is not broken here."""
    response = await client.get("/healthz", headers={"X-Request-ID": "trace-vindo-do-proxy"})

    assert response.headers["X-Request-ID"] == "trace-vindo-do-proxy"


async def test_a_generated_request_id_is_returned_when_none_is_sent(
    client: AsyncClient,
) -> None:
    response = await client.get("/healthz")

    assert response.headers["X-Request-ID"]


# --- response headers ------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        # The API is never framed; the widget page is, but it is served by the frontend.
        ("X-Frame-Options", "DENY"),
    ],
)
async def test_security_headers_are_present(client: AsyncClient, header: str, value: str) -> None:
    response = await client.get("/healthz")

    assert response.headers[header] == value


# --- payload limits --------------------------------------------------------


async def test_an_oversized_body_is_rejected_before_parsing(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    huge = "a" * (MAX_REQUEST_BYTES + 1_000)

    response = await client.post(
        "/api/v1/tutors",
        json={"title": "Tutor", "system_instructions": huge},
        headers=auth_headers,
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_a_normal_body_still_passes(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/tutors",
        json={"title": "Tutor", "system_instructions": "Instrucoes suficientes para passar."},
        headers=auth_headers,
    )

    assert response.status_code == 201


# --- retention -------------------------------------------------------------


async def _make_session(session: AsyncSession, *, expired: bool) -> str:
    tutor = Tutor(title="T", slug=f"t-{expired}", system_instructions="Instrucoes.")
    key = EmbedKey(tutor=tutor, public_key=f"pk_live_{expired}", allowed_origins=[])
    chat = ChatSession(
        tutor=tutor,
        embed_key=key,
        origin="https://cliente.com",
        expires_at=utcnow() - timedelta(hours=1) if expired else utcnow() + timedelta(hours=1),
    )
    chat.messages.append(ChatMessage(role=MessageRole.USER, content="oi"))
    session.add(chat)
    await session.commit()
    return chat.id


async def test_cleanup_removes_only_expired_sessions_and_their_messages(
    session: AsyncSession,
) -> None:
    await _make_session(session, expired=True)
    alive_id = await _make_session(session, expired=False)

    removed = await ChatRepository(session).delete_expired_sessions()
    await session.commit()

    remaining = await session.execute(select(ChatSession.id))
    message_count = await session.execute(select(func.count()).select_from(ChatMessage))

    assert removed == 1
    assert [row[0] for row in remaining] == [alive_id]
    # The cascade must take the messages with the session, or retention is theatre.
    assert message_count.scalar_one() == 1
