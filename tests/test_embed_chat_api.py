"""The public embed runtime: session, chat and streaming (PRD 3.3, 3.4 and 4.4.2)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from httpx import AsyncClient
from pydantic_ai import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from app.api.deps import reset_rate_limiters
from app.core.config import Settings

Headers = dict[str, str]
CLIENT_ORIGIN = "https://cliente.com"


async def _seed_embed(
    client: AsyncClient, auth_headers: Headers, *, origins: list[str] | None = None, **tutor: Any
) -> tuple[str, str]:
    """Create a tutor plus an embed key. Returns `(tutor_id, public_key)`."""
    payload: dict[str, Any] = {
        "title": "Tutor de Politicas",
        "system_instructions": "Responda com base nas fontes configuradas.",
        "sources": [
            {
                "kind": "inline_text",
                "label": "Politica de trabalho remoto",
                "content": (
                    "# Politica\n\n## Auxilio home office\n"
                    "O auxilio e de R$ 150,00 por mes.\n\n"
                    "## Ferias\nAs ferias seguem a CLT: 30 dias."
                ),
            }
        ],
    }
    payload.update(tutor)

    created = await client.post("/api/v1/tutors", json=payload, headers=auth_headers)
    assert created.status_code == 201, created.text
    tutor_id = created.json()["id"]

    key = await client.post(
        f"/api/v1/tutors/{tutor_id}/embed-keys",
        json={"allowed_origins": origins if origins is not None else [CLIENT_ORIGIN]},
        headers=auth_headers,
    )
    assert key.status_code == 201, key.text
    return tutor_id, key.json()["public_key"]


async def _open_session(
    client: AsyncClient, public_key: str, *, origin: str = CLIENT_ORIGIN
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/embed/session", json={"embed_key": public_key}, headers={"Origin": origin}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _bearer(session: dict[str, Any]) -> Headers:
    return {"Authorization": f"Bearer {session['session_token']}", "Origin": CLIENT_ORIGIN}


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into `(event, data)` pairs.

    Written by hand on purpose: it asserts the framing (`event:`/`data:` and the blank-line
    terminator) that a real EventSource client depends on.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in raw.split("\n\n"):
        if not frame.strip():
            continue
        name = ""
        data = ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        events.append((name, json.loads(data)))
    return events


# --- session ---------------------------------------------------------------


async def test_opening_a_session_returns_a_scoped_token_and_the_public_profile(
    client: AsyncClient, auth_headers: Headers
) -> None:
    _, public_key = await _seed_embed(client, auth_headers)

    body = await _open_session(client, public_key)

    assert body["session_token"]
    assert body["expires_in"] > 0
    assert body["tutor"]["title"] == "Tutor de Politicas"
    assert body["history"] == []


async def test_the_session_response_never_leaks_the_tutor_instructions(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """The prompt an administrator wrote must not reach a public page."""
    _, public_key = await _seed_embed(client, auth_headers)

    response = await client.post(
        "/api/v1/embed/session",
        json={"embed_key": public_key},
        headers={"Origin": CLIENT_ORIGIN},
    )

    assert "system_instructions" not in response.text
    assert "Responda com base nas fontes" not in response.text


async def test_an_unlisted_origin_cannot_open_a_session(
    client: AsyncClient, auth_headers: Headers
) -> None:
    _, public_key = await _seed_embed(client, auth_headers)

    response = await client.post(
        "/api/v1/embed/session",
        json={"embed_key": public_key},
        headers={"Origin": "https://atacante.com"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


async def test_an_unknown_key_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/embed/session",
        json={"embed_key": "pk_live_inexistente"},
        headers={"Origin": CLIENT_ORIGIN},
    )

    assert response.status_code == 404


async def test_a_deactivated_tutor_reports_unavailability(
    client: AsyncClient, auth_headers: Headers
) -> None:
    tutor_id, public_key = await _seed_embed(client, auth_headers)
    await client.post(f"/api/v1/tutors/{tutor_id}/deactivate", headers=auth_headers)

    response = await client.post(
        "/api/v1/embed/session",
        json={"embed_key": public_key},
        headers={"Origin": CLIENT_ORIGIN},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TUTOR_INACTIVE"


async def test_chat_requires_a_session_token(client: AsyncClient) -> None:
    response = await client.post("/api/v1/embed/chat", json={"message": "oi"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"


async def test_an_admin_token_cannot_be_used_as_a_session_token(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.post("/api/v1/embed/chat", json={"message": "oi"}, headers=auth_headers)

    assert response.status_code == 401


# --- framing policy --------------------------------------------------------


async def test_embed_config_exposes_only_the_framing_policy(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """Feeds the frontend's `frame-ancestors` header; must leak nothing about the tutor."""
    _, public_key = await _seed_embed(client, auth_headers)

    response = await client.get(f"/api/v1/embed/config?embed_key={public_key}")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "allowed_origins": [CLIENT_ORIGIN],
        "allows_any_origin": False,
        "is_active": True,
    }
    assert "Politica" not in response.text


async def test_embed_config_reports_a_key_without_an_allowlist(
    client: AsyncClient, auth_headers: Headers, settings: Settings
) -> None:
    """"Any origin" must be distinguishable from "unknown", or the CSP cannot be built."""
    settings.embed_default_origins = ""
    _, public_key = await _seed_embed(client, auth_headers, origins=[])

    body = (await client.get(f"/api/v1/embed/config?embed_key={public_key}")).json()

    assert body["allows_any_origin"] is True


async def test_embed_config_still_answers_for_a_revoked_key(
    client: AsyncClient, auth_headers: Headers
) -> None:
    tutor_id, public_key = await _seed_embed(client, auth_headers)
    keys = await client.get(f"/api/v1/tutors/{tutor_id}/embed-keys", headers=auth_headers)
    await client.post(f"/api/v1/embed-keys/{keys.json()[0]['id']}/revoke", headers=auth_headers)

    body = (await client.get(f"/api/v1/embed/config?embed_key={public_key}")).json()

    assert body["is_active"] is False


async def test_embed_config_of_an_unknown_key_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/embed/config?embed_key=pk_live_nao_existe")

    assert response.status_code == 404


# --- chat ------------------------------------------------------------------


async def test_chat_without_streaming_returns_the_full_answer(
    client: AsyncClient, auth_headers: Headers
) -> None:
    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    response = await client.post(
        "/api/v1/embed/chat",
        json={"message": "Qual o auxilio?", "stream": False},
        headers=_bearer(session),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Resposta de teste."
    assert body["message_id"]


async def test_chat_streams_sse_frames(client: AsyncClient, auth_headers: Headers) -> None:
    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    response = await client.post(
        "/api/v1/embed/chat", json={"message": "Qual o auxilio?"}, headers=_bearer(session)
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Without this, nginx would buffer the whole answer and defeat the streaming.
    assert response.headers["x-accel-buffering"] == "no"

    events = _parse_sse(response.text)
    names = [name for name, _ in events]

    assert "token" in names
    assert names[-1] == "done"
    assert "".join(data["delta"] for name, data in events if name == "token") == (
        "Resposta de teste."
    )


async def test_the_message_is_validated(client: AsyncClient, auth_headers: Headers) -> None:
    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    empty = await client.post("/api/v1/embed/chat", json={"message": ""}, headers=_bearer(session))
    too_long = await client.post(
        "/api/v1/embed/chat", json={"message": "a" * 2_001}, headers=_bearer(session)
    )

    assert empty.status_code == 422
    assert too_long.status_code == 422


# --- history ---------------------------------------------------------------


async def test_both_turns_are_persisted(client: AsyncClient, auth_headers: Headers) -> None:
    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    await client.post(
        "/api/v1/embed/chat",
        json={"message": "primeira pergunta", "stream": False},
        headers=_bearer(session),
    )

    history = await client.get("/api/v1/embed/messages", headers=_bearer(session))

    assert history.status_code == 200
    roles = [message["role"] for message in history.json()]
    contents = [message["content"] for message in history.json()]
    assert roles == ["user", "assistant"]
    assert contents[0] == "primeira pergunta"


async def test_history_is_capped_and_returned_in_chronological_order(
    client: AsyncClient, auth_headers: Headers, settings: Settings
) -> None:
    """PRD 4.4.2: keep the last N messages for continuity, not the whole transcript."""
    settings.history_max_messages = 4
    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    for index in range(5):
        await client.post(
            "/api/v1/embed/chat",
            json={"message": f"pergunta {index}", "stream": False},
            headers=_bearer(session),
        )

    history = await client.get("/api/v1/embed/messages", headers=_bearer(session))
    messages = history.json()

    assert len(messages) == 4
    # The newest window, oldest first.
    assert messages[0]["content"] == "pergunta 3"
    assert messages[-1]["content"] == "Resposta de teste."


class _HistorySpy:
    """Captures how many messages the model received on each call."""

    def __init__(self) -> None:
        self.counts: list[int] = []

    def model(self) -> FunctionModel:
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            self.counts.append(len(messages))
            yield "ok"

        return FunctionModel(stream_function=respond)


async def test_previous_turns_are_replayed_into_the_agent(
    client: AsyncClient, auth_headers: Headers, app: Any, settings: Settings
) -> None:
    from app.agent.pydantic_ai_runner import PydanticAIRunner

    spy = _HistorySpy()
    app.state.agent_runner = PydanticAIRunner(settings=settings, model=spy.model())

    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    for index in range(3):
        await client.post(
            "/api/v1/embed/chat",
            json={"message": f"pergunta {index}", "stream": False},
            headers=_bearer(session),
        )

    # Each turn adds one user and one assistant message to the replayed context.
    assert spy.counts == sorted(spy.counts)
    assert spy.counts[-1] > spy.counts[0]


# --- the knowledge loop, end to end ---------------------------------------


class _ToolScript:
    def __init__(self, source_getter: Any) -> None:
        self._source_getter = source_getter
        self.used = False

    def model(self) -> FunctionModel:
        async def respond(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            if not self.used:
                self.used = True
                yield {
                    0: DeltaToolCall(
                        name="search_source",
                        json_args=json.dumps(
                            {"source_id": self._source_getter(), "query": "auxilio home office"}
                        ),
                    )
                }
                return
            yield "O auxilio e de R$ 150,00."

        return FunctionModel(stream_function=respond)


async def test_the_full_embed_flow_reports_tools_and_citations(
    client: AsyncClient, auth_headers: Headers, app: Any, settings: Settings
) -> None:
    """The end-to-end path the interview demo walks through."""
    from app.agent.pydantic_ai_runner import PydanticAIRunner

    tutor_id, public_key = await _seed_embed(client, auth_headers)
    detail = await client.get(f"/api/v1/tutors/{tutor_id}", headers=auth_headers)
    source_id = detail.json()["sources"][0]["id"]

    script = _ToolScript(lambda: source_id)
    app.state.agent_runner = PydanticAIRunner(settings=settings, model=script.model())

    session = await _open_session(client, public_key)
    response = await client.post(
        "/api/v1/embed/chat",
        json={"message": "Qual o valor do auxilio home office?"},
        headers=_bearer(session),
    )

    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    done = next(data for name, data in events if name == "done")

    assert "tool_started" in names
    assert names.index("tool_started") < names.index("done")
    assert done["content"] == "O auxilio e de R$ 150,00."
    assert [citation["label"] for citation in done["citations"]] == ["Politica de trabalho remoto"]
    assert done["tool_calls"][0]["name"] == "search_source"


async def test_an_agent_failure_is_reported_as_an_sse_error_event(
    client: AsyncClient, auth_headers: Headers, app: Any, settings: Settings
) -> None:
    """Once the response has started there is no status code left to change."""
    from app.agent.pydantic_ai_runner import PydanticAIRunner

    async def explode(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        msg = "modelo indisponivel"
        raise RuntimeError(msg)
        yield ""  # pragma: no cover

    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)
    app.state.agent_runner = PydanticAIRunner(
        settings=settings, model=FunctionModel(stream_function=explode)
    )

    response = await client.post(
        "/api/v1/embed/chat", json={"message": "oi"}, headers=_bearer(session)
    )

    assert response.status_code == 200
    name, data = _parse_sse(response.text)[-1]
    assert name == "error"
    assert data["code"] == "AGENT_FAILED"
    assert "modelo indisponivel" not in response.text


# --- rate limiting ---------------------------------------------------------


async def test_the_chat_rate_limit_answers_429_with_retry_after(
    client: AsyncClient, auth_headers: Headers, settings: Settings
) -> None:
    settings.rate_limit_chat_per_minute = 3
    reset_rate_limiters()

    _, public_key = await _seed_embed(client, auth_headers)
    session = await _open_session(client, public_key)

    statuses = []
    for _ in range(4):
        response = await client.post(
            "/api/v1/embed/chat",
            json={"message": "oi", "stream": False},
            headers=_bearer(session),
        )
        statuses.append(response.status_code)

    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429

    blocked = await client.post(
        "/api/v1/embed/chat", json={"message": "oi", "stream": False}, headers=_bearer(session)
    )
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["Retry-After"]) > 0


# --- CORS ------------------------------------------------------------------


async def test_the_embed_api_echoes_any_origin(client: AsyncClient, auth_headers: Headers) -> None:
    """CORS is a browser read-protection, not authorisation: the Origin check is server-side."""
    _, public_key = await _seed_embed(client, auth_headers)

    response = await client.post(
        "/api/v1/embed/session",
        json={"embed_key": public_key},
        headers={"Origin": CLIENT_ORIGIN},
    )

    assert response.headers["access-control-allow-origin"] == CLIENT_ORIGIN
    # No credentials: the session token travels in the Authorization header, not a cookie.
    assert "access-control-allow-credentials" not in response.headers


async def test_the_admin_api_refuses_an_unknown_origin(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tutors", headers={"Origin": "https://atacante.com"})

    assert "access-control-allow-origin" not in response.headers


async def test_preflight_is_answered_for_the_embed_api(client: AsyncClient) -> None:
    response = await client.request(
        "OPTIONS",
        "/api/v1/embed/chat",
        headers={
            "Origin": CLIENT_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == CLIENT_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "Authorization" in response.headers["access-control-allow-headers"]
