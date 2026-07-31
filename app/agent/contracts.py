"""The boundary between the application and whichever agent framework is in use.

Nothing outside `app/agent/` imports `pydantic_ai`. The chat service talks to `AgentRunner`,
and the concrete runner is chosen by configuration. That is what makes decision D1 (Pydantic AI
over LangChain) reversible instead of a bet, and what allows shipping a LangGraph runner behind
the same interface for comparison.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.db.models.tutor import Tutor
from app.services.source_service import SourceService


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    """One past turn, framework-agnostic."""

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class Citation:
    source_id: str
    label: str
    url: str | None
    snippet: str


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Record of one tool call, for the UI and for the logs."""

    name: str
    source_label: str | None
    duration_ms: int
    ok: bool
    detail: str | None = None


class AgentEventKind(StrEnum):
    # "token" here is a slice of generated text, not a credential.
    TOKEN = "token"  # noqa: S105
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """A single item of the streamed response.

    Tool events exist so the widget can show "consulting *Guia de Produto*…" — which is how the
    agentic knowledge strategy becomes visible to a user instead of an implementation detail.
    """

    kind: AgentEventKind
    text: str = ""
    tool_name: str | None = None
    source_label: str | None = None
    citations: tuple[Citation, ...] = ()
    tool_calls: tuple[ToolInvocation, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None


@dataclass(slots=True)
class AgentDeps:
    """Everything a tool needs at run time, injected per request."""

    tutor: Tutor
    sources: SourceService
    session_id: str
    max_tool_calls: int
    invocations: list[ToolInvocation] = field(default_factory=list)
    citations: dict[str, Citation] = field(default_factory=dict)


@runtime_checkable
class AgentRunner(Protocol):
    """Contract every agent implementation must satisfy."""

    name: str

    def stream(
        self,
        *,
        user_message: str,
        history: Sequence[HistoryMessage],
        deps: AgentDeps,
    ) -> AsyncIterator[AgentEvent]:
        """Yield events until the answer is complete.

        Implementations must not raise for expected failures (timeout, model error): they emit
        an `ERROR` event so the transport can close the stream cleanly.
        """
        ...
