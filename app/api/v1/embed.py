"""Public embed runtime: session and chat (PRD 3.3).

Transport decision (D2): **HTTP + Server-Sent Events**, not WebSocket. The conversation is
strictly request/response with a streamed answer, so bidirectionality would buy nothing while
costing connection state, a second auth path and a harder test setup. SSE streams token by
token, survives proxies and reconnects trivially.

The same logic is exposed without streaming (`"stream": false`) for tests and server-to-server
callers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from app.agent.contracts import AgentEvent, AgentEventKind
from app.api.deps import (
    ChatServiceDep,
    CurrentEmbedSession,
    OriginDep,
    SessionDep,
    SettingsDep,
    embed_rate_limiter,
    session_rate_limiter,
)
from app.core.errors import AgentExecutionError
from app.core.logging import get_logger
from app.core.security import create_embed_session_token
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    MessageRead,
    SessionCreate,
    SessionResponse,
    TutorPublicProfile,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/embed", tags=["embed"])


@router.post(
    "/session",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Abre uma sessao de conversa para o widget",
    description=(
        "Valida a chave publica de embed contra a lista de origens permitidas e devolve um "
        "token de sessao de curta duracao. Nenhum segredo do backend e exposto."
    ),
    responses={
        401: {"description": "Chave de embed invalida ou revogada"},
        403: {"description": "Origem nao autorizada para esta chave"},
        409: {"description": "Tutor desativado"},
        429: {"description": "Muitas aberturas de sessao"},
    },
)
async def open_session(
    payload: SessionCreate,
    request: Request,
    origin: OriginDep,
    service: ChatServiceDep,
    settings: SettingsDep,
    session: SessionDep,
) -> SessionResponse:
    session_rate_limiter(settings).check(_client_key(request))

    chat_session, tutor, history = await service.open_session(payload, origin=origin)
    token, expires_in = create_embed_session_token(
        settings,
        session_id=chat_session.id,
        tutor_id=tutor.id,
        embed_key_id=chat_session.embed_key_id,
    )
    await session.commit()

    return SessionResponse(
        session_token=token,
        expires_in=expires_in,
        tutor=TutorPublicProfile(
            id=tutor.id,
            title=tutor.title,
            description=tutor.description,
            greeting=tutor.greeting,
        ),
        history=[MessageRead.model_validate(message) for message in history],
    )


@router.get(
    "/messages",
    response_model=list[MessageRead],
    summary="Historico da sessao atual",
)
async def list_messages(
    chat_session: CurrentEmbedSession, service: ChatServiceDep
) -> list[MessageRead]:
    messages = await service.history(chat_session.id)
    return [MessageRead.model_validate(message) for message in messages]


@router.post(
    "/chat",
    summary="Envia uma mensagem ao tutor",
    description=(
        "Por padrao devolve text/event-stream com os eventos token, tool, done e error. "
        'Com "stream": false devolve a resposta completa em JSON.'
    ),
    responses={
        200: {
            "model": ChatResponse,
            "description": (
                "Com stream=false, o corpo e o JSON descrito abaixo. Com stream=true (padrao), "
                "e um text/event-stream com os eventos token, tool_started, tool_finished, "
                "done e error."
            ),
            # Declared alongside the model so the OpenAPI document admits both media types.
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        401: {"description": "Token de sessao ausente, invalido ou expirado"},
        429: {"description": "Muitas mensagens nesta sessao"},
    },
)
async def chat(
    payload: ChatRequest,
    request: Request,
    chat_session: CurrentEmbedSession,
    service: ChatServiceDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    embed_rate_limiter(settings).check(chat_session.id)

    if not payload.stream:
        return await _answer_json(payload, chat_session, service, session)

    return StreamingResponse(
        _event_stream(payload, chat_session, service, session),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which would hold the whole answer
            # back and defeat the point of streaming.
            "X-Accel-Buffering": "no",
        },
    )


# --- transports ------------------------------------------------------------


async def _event_stream(
    payload: ChatRequest,
    chat_session: Any,
    service: Any,
    session: Any,
) -> AsyncIterator[str]:
    try:
        async for event in service.answer(chat_session, payload.message):
            yield _sse(event)
        await session.commit()
    except Exception:
        # The response has already started, so there is no status code left to change: the
        # only honest thing is to emit an error event and close cleanly.
        await session.rollback()
        logger.exception("chat_stream_failed", session_id=chat_session.id)
        yield _sse(
            AgentEvent(
                kind=AgentEventKind.ERROR,
                error_code=AgentExecutionError.code,
                text=AgentExecutionError.message,
            )
        )


async def _answer_json(
    payload: ChatRequest, chat_session: Any, service: Any, session: Any
) -> Response:
    final: AgentEvent | None = None
    async for event in service.answer(chat_session, payload.message):
        if event.kind in (AgentEventKind.DONE, AgentEventKind.ERROR):
            final = event

    if final is None or final.kind is AgentEventKind.ERROR:
        await session.rollback()
        raise AgentExecutionError(
            final.text if final else AgentExecutionError.message,
            code=final.error_code if final and final.error_code else AgentExecutionError.code,
        )

    await session.commit()
    body = ChatResponse(
        message_id=str(final.usage.get("message_id", "")),
        content=final.text,
        citations=[_citation_dict(citation) for citation in final.citations],
        tool_calls=[_tool_dict(call) for call in final.tool_calls],
    )
    return Response(content=body.model_dump_json(), media_type="application/json", status_code=200)


def _sse(event: AgentEvent) -> str:
    """Render one Server-Sent Event.

    The double newline is the frame terminator; getting it wrong makes the client wait forever
    for an event that has in fact already been sent.
    """
    return f"event: {event.kind.value}\ndata: {json.dumps(_payload(event), ensure_ascii=False)}\n\n"


def _payload(event: AgentEvent) -> dict[str, Any]:
    if event.kind is AgentEventKind.TOKEN:
        return {"delta": event.text}

    if event.kind in (AgentEventKind.TOOL_STARTED, AgentEventKind.TOOL_FINISHED):
        return {"tool": event.tool_name, "source": event.source_label}

    if event.kind is AgentEventKind.DONE:
        return {
            "message_id": event.usage.get("message_id"),
            "content": event.text,
            "citations": [_citation_dict(citation) for citation in event.citations],
            "tool_calls": [_tool_dict(call) for call in event.tool_calls],
        }

    return {"code": event.error_code, "message": event.text}


def _citation_dict(citation: Any) -> dict[str, Any]:
    return {
        "source_id": citation.source_id,
        "label": citation.label,
        "url": citation.url,
        "snippet": citation.snippet,
    }


def _tool_dict(call: Any) -> dict[str, Any]:
    return {
        "name": call.name,
        "source": call.source_label,
        "duration_ms": call.duration_ms,
        "ok": call.ok,
    }


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client else "desconhecido"
