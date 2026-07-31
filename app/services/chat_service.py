"""Conversation orchestration (PRD 3.3 and 4.4.2).

Ties together: authorising the embed, opening a session, replaying the last N messages into the
agent, streaming the answer out and persisting both turns.

The service is transport-agnostic — it yields `AgentEvent`s and knows nothing about SSE — so the
same code serves the streaming and the JSON endpoints, and can be tested without HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timedelta

from app.agent.contracts import (
    AgentDeps,
    AgentEvent,
    AgentEventKind,
    AgentRunner,
    ChatRole,
    HistoryMessage,
    ModelOverrides,
)
from app.core.config import Settings
from app.core.errors import (
    EmbedKeyRevokedError,
    OriginNotAllowedError,
    SessionExpiredError,
    TutorInactiveError,
)
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models.chat import ChatMessage, ChatSession, MessageRole
from app.db.models.tutor import Tutor
from app.repositories.chat import ChatRepository
from app.schemas.chat import SessionCreate
from app.services.embed_service import EmbedService
from app.services.source_service import SourceService
from app.utils.origins import origin_matches

logger = get_logger(__name__)


class ChatService:
    def __init__(
        self,
        *,
        chats: ChatRepository,
        embeds: EmbedService,
        sources: SourceService,
        runner: AgentRunner,
        settings: Settings,
    ) -> None:
        self._chats = chats
        self._embeds = embeds
        self._sources = sources
        self._runner = runner
        self._settings = settings

    # --- session -----------------------------------------------------------

    async def open_session(
        self, payload: SessionCreate, *, origin: str | None
    ) -> tuple[ChatSession, Tutor, list[ChatMessage]]:
        """Authorise the embed and start a conversation.

        A brand-new session is created on every widget load. Sessions are anonymous by design —
        the iframe has no user identity — so there is nothing to resume across page loads, and
        pretending otherwise would be a privacy problem rather than a feature.
        """
        key, tutor = await self._embeds.authorize(payload.embed_key, origin)

        chat_session = ChatSession(
            tutor_id=tutor.id,
            embed_key_id=key.id,
            origin=origin or "",
            expires_at=utcnow() + timedelta(minutes=self._settings.embed_session_ttl_minutes),
        )
        self._chats.add_session(chat_session)
        await self._chats.flush()

        logger.info(
            "embed_session_opened",
            session_id=chat_session.id,
            tutor_id=tutor.id,
            embed_key_id=key.id,
            origin=origin,
        )
        return chat_session, tutor, []

    async def resolve_session(self, session_id: str) -> ChatSession:
        """Re-authorise an open session on every request.

        A session token is a bearer credential valid for its whole TTL, so anything an
        administrator revokes has to be re-checked here — otherwise "revoke this key" would
        really mean "revoke it in up to `EMBED_SESSION_TTL_MINUTES`", which is not what the word
        promises and not what a demo of it would show.
        """
        chat_session = await self._chats.get_session(session_id)
        if chat_session is None or chat_session.is_expired():
            raise SessionExpiredError

        key = chat_session.embed_key
        if not key.is_active:
            logger.info(
                "embed_session_key_revoked", session_id=chat_session.id, embed_key_id=key.id
            )
            raise EmbedKeyRevokedError

        # The origin recorded when the session opened is re-tested against the *current*
        # allowlist, so removing a domain takes effect immediately.
        if not origin_matches(chat_session.origin or None, key.allowed_origins):
            logger.warning(
                "embed_session_origin_revoked",
                session_id=chat_session.id,
                embed_key_id=key.id,
                origin=chat_session.origin,
            )
            raise OriginNotAllowedError

        if not chat_session.tutor.is_active:
            raise TutorInactiveError

        chat_session.last_seen_at = utcnow()
        return chat_session

    async def history(self, session_id: str) -> list[ChatMessage]:
        return await self._chats.recent_messages(
            session_id, limit=self._settings.history_max_messages
        )

    # --- conversation ------------------------------------------------------

    async def answer(
        self, chat_session: ChatSession, user_message: str
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn, streaming events and persisting the result.

        The user message is stored *before* the run so an interrupted answer still leaves a
        coherent transcript; the assistant message is stored when the run completes.
        """
        tutor = chat_session.tutor

        self._chats.add_message(
            ChatMessage(session_id=chat_session.id, role=MessageRole.USER, content=user_message)
        )
        await self._chats.flush()

        history = _to_history(
            await self._chats.recent_messages(
                chat_session.id, limit=self._settings.history_max_messages
            ),
            # The message just stored is the prompt of this run, not part of its history.
            drop_last=True,
        )

        deps = AgentDeps(
            tutor=tutor,
            sources=self._sources,
            session_id=chat_session.id,
            max_tool_calls=_max_tool_calls(tutor, self._settings),
            overrides=ModelOverrides.from_mapping(tutor.model_settings),
        )

        started = utcnow()
        async for event in self._runner.stream(
            user_message=user_message, history=history, deps=deps
        ):
            if event.kind is AgentEventKind.DONE:
                await self._persist_answer(chat_session, event, started_at=started)
            yield event

    async def _persist_answer(
        self, chat_session: ChatSession, event: AgentEvent, *, started_at: datetime
    ) -> None:
        latency_ms = int((utcnow() - started_at).total_seconds() * 1000)

        message = ChatMessage(
            session_id=chat_session.id,
            role=MessageRole.ASSISTANT,
            content=event.text,
            citations=[asdict(citation) for citation in event.citations] or None,
            tool_calls=[asdict(call) for call in event.tool_calls] or None,
            latency_ms=latency_ms,
            token_usage=event.usage or None,
        )
        self._chats.add_message(message)
        chat_session.last_seen_at = utcnow()
        await self._chats.flush()

        # The transport needs the persisted id to report it to the client.
        event.usage.setdefault("message_id", message.id)

        logger.info(
            "chat_answered",
            session_id=chat_session.id,
            tutor_id=chat_session.tutor_id,
            message_id=message.id,
            latency_ms=latency_ms,
            tool_calls=len(event.tool_calls),
            citations=len(event.citations),
        )


def _to_history(messages: list[ChatMessage], *, drop_last: bool = False) -> list[HistoryMessage]:
    usable = messages[:-1] if drop_last and messages else messages
    return [
        HistoryMessage(
            role=ChatRole.USER if message.role is MessageRole.USER else ChatRole.ASSISTANT,
            content=message.content,
        )
        for message in usable
        if message.role is not MessageRole.SYSTEM and message.content
    ]


def _max_tool_calls(tutor: Tutor, settings: Settings) -> int:
    configured = tutor.model_settings.get("max_tool_calls")
    if isinstance(configured, int) and configured > 0:
        return min(configured, 20)
    return settings.agent_max_tool_calls
