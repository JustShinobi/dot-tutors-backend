"""The boundary between the application and whichever agent framework is in use.

Nothing outside `app/agent/` imports `pydantic_ai`. The chat service talks to `AgentRunner`,
and the concrete runner is chosen by configuration. That is what makes decision D1 (Pydantic AI
over LangChain) reversible instead of a bet, and what allows shipping a LangGraph runner behind
the same interface for comparison.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
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


@dataclass(frozen=True, slots=True)
class ModelOverrides:
    """Per-tutor model configuration, resolved from `tutor.model_settings`.

    Expressed here rather than as framework types so both runners consume the same thing, and
    so the administrative API is not silently accepting settings nothing reads.
    """

    model: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ModelOverrides:
        """Read what the administrator configured, ignoring anything malformed.

        A bad value in this column must degrade to the global default, never break the
        conversation: the column is JSON and could hold a row written by an older schema.
        """
        return cls(
            model=_as_str(raw.get("model")),
            temperature=_as_float(raw.get("temperature")),
            max_output_tokens=_as_int(raw.get("max_output_tokens")),
        )


def _as_str(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


@dataclass(slots=True)
class AgentDeps:
    """Everything a tool needs at run time, injected per request."""

    tutor: Tutor
    sources: SourceService
    session_id: str
    max_tool_calls: int
    overrides: ModelOverrides = field(default_factory=ModelOverrides)
    invocations: list[ToolInvocation] = field(default_factory=list)
    citations: dict[str, Citation] = field(default_factory=dict)

    def label_for(self, source_id: str | None) -> str | None:
        """Human-readable name of a source the model just asked for.

        The model calls tools with source *ids*; the widget shows "consulting <name>". Resolving
        it here — from the tutor already loaded in memory — avoids both a query and the earlier
        behaviour of sending a raw UUID under a field called `source_label`.
        """
        if not source_id:
            return None
        for source in self.tutor.sources:
            if source.id == source_id:
                return source.label
        return None


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
