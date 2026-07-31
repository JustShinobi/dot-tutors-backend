"""Data access for chat sessions and messages."""

from __future__ import annotations

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import utcnow
from app.db.models.chat import ChatMessage, ChatSession


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- sessions ----------------------------------------------------------

    async def get_session(self, session_id: str) -> ChatSession | None:
        result = await self._session.execute(
            select(ChatSession)
            .where(ChatSession.id == session_id)
            # The embed key is loaded eagerly because every request re-checks it: a revoked key
            # or a shrunken origin allowlist has to stop an *already open* session, not just
            # the next one.
            .options(selectinload(ChatSession.tutor), selectinload(ChatSession.embed_key))
        )
        return result.scalar_one_or_none()

    def add_session(self, chat_session: ChatSession) -> ChatSession:
        self._session.add(chat_session)
        return chat_session

    async def recent_sessions_for_tutor(self, tutor_id: str, *, limit: int) -> list[ChatSession]:
        """Most recently active sessions of a tutor, with their messages.

        The messages are eager-loaded because the caller serialises them immediately; leaving
        them lazy would fire one query per session from inside an async context.
        """
        result = await self._session.execute(
            select(ChatSession)
            .where(ChatSession.tutor_id == tutor_id)
            .order_by(ChatSession.last_seen_at.desc())
            .limit(limit)
            .options(selectinload(ChatSession.messages))
        )
        return list(result.scalars().unique())

    # --- messages ----------------------------------------------------------

    async def recent_messages(self, session_id: str, *, limit: int) -> list[ChatMessage]:
        """Return the last `limit` messages in chronological order.

        The query takes the *newest* rows and the result is reversed in Python: ordering by
        `created_at` ascending with a LIMIT would return the oldest ones, which is the opposite
        of the continuity we need.
        """
        result = await self._session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
        return list(reversed(list(result.scalars().unique())))

    def add_message(self, message: ChatMessage) -> ChatMessage:
        self._session.add(message)
        return message

    async def delete_expired_sessions(self) -> int:
        """Retention: drop sessions past their expiry, cascading to their messages."""
        result = await self._session.execute(
            delete(ChatSession).where(ChatSession.expires_at < utcnow())
        )
        # `execute` is typed as returning Result; a DELETE always yields a CursorResult, which
        # is the only kind that carries `rowcount`.
        assert isinstance(result, CursorResult)
        return int(result.rowcount or 0)

    async def flush(self) -> None:
        await self._session.flush()
