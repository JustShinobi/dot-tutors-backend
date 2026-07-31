"""Schemas of the public embed runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.chat import MessageRole


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embed_key: str = Field(min_length=8, max_length=120)


class TutorPublicProfile(BaseModel):
    """What the widget is allowed to know about the tutor.

    Deliberately narrow: `system_instructions` is *not* here. Those are the configuration the
    administrator wrote, and shipping them to a public page would leak the tutor's prompt.
    """

    id: str
    title: str
    description: str
    greeting: str | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: MessageRole
    content: str
    citations: list[dict[str, Any]] | None = None
    created_at: datetime


class SessionResponse(BaseModel):
    session_token: str
    token_type: str = "bearer"  # noqa: S105  (OAuth scheme, not a credential)
    expires_in: int
    tutor: TutorPublicProfile
    history: list[MessageRead]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=2_000)]
    stream: bool = Field(
        default=True,
        description=(
            "true devolve text/event-stream com a resposta token a token; false devolve JSON "
            "completo, util para testes e integracoes server-to-server."
        ),
    )


class ChatResponse(BaseModel):
    """Non-streaming answer."""

    message_id: str
    content: str
    citations: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


class SessionSummary(BaseModel):
    """One past conversation, for the administrator's inspection view.

    Administrative, not public: it carries the origin and the full transcript, neither of which
    belongs in a response the widget can read.
    """

    id: str
    origin: str
    created_at: datetime
    last_seen_at: datetime
    message_count: int
    messages: list[MessageRead]
