"""Conversation session and message history (PRD 4.4.2).

Only the last N messages of a session are replayed into the agent; the rest stays in the
database for inspection. There is no user account behind a session — the iframe is anonymous —
so a session is scoped to (tutor, embed key, origin) and expires.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
    str_enum_column,
    utcnow,
)

if TYPE_CHECKING:
    from app.db.models.embed import EmbedKey
    from app.db.models.tutor import Tutor


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    tutor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tutors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embed_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("embed_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    tutor: Mapped[Tutor] = relationship()
    embed_key: Mapped[EmbedKey] = relationship()
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.expires_at

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ChatSession {self.id} tutor={self.tutor_id}>"


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_session_created", "session_id", "created_at"),)

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(str_enum_column(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # [{"name": "search_source", "source_id": "...", "ms": 42, "ok": true}] - what the agent
    # actually consulted to produce this answer (PRD 5.2).
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)
    # [{"source_id": "...", "label": "...", "url": "...", "snippet": "..."}]
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ChatMessage {self.role} len={len(self.content)}>"
