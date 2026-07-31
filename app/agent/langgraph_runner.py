"""The same agent, built on LangGraph — for comparison, not for production use here.

This module exists to make decision D1 (Pydantic AI over LangChain) *comparative* instead of a
bet made from unfamiliarity. It implements the exact same `AgentRunner` contract, over the exact
same tools in `app.agent.tools`, so the two can be swapped with `AGENT_RUNNER=langgraph` and
measured against each other.

Enable with the optional extra:

    pip install -e ".[langgraph]"

What the comparison actually showed (written up in the README):

* **The knowledge layer did not move.** `SourceService` and `app/agent/tools.py` are untouched;
  only the wiring differs. That is the evidence that the tools were built as domain logic rather
  than as framework artefacts.
* **Streaming is where the cost is.** Pydantic AI's `run_stream_events` yields text deltas and
  tool events on one iterator. Here the same result takes `astream_events`, filtering by event
  name and version, and hand-mapping chunk shapes.
* **Testing is the real difference.** `FunctionModel` scripts a model's tool calls in a few
  lines; the equivalent here means a fake chat model implementing the LangChain interface.

None of this makes LangGraph a bad tool — it is a strong one for stateful, multi-node graphs.
It is simply more machinery than a single tool-calling loop needs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agent import tools as knowledge_tools
from app.agent.contracts import (
    AgentDeps,
    AgentEvent,
    AgentEventKind,
    ChatRole,
    HistoryMessage,
    ModelOverrides,
)
from app.agent.prompts import build_instructions, format_source_catalogue
from app.agent.retry import backoff_delay, is_retryable
from app.core.config import Settings
from app.core.errors import AgentExecutionError, AgentTimeoutError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _build_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider != "google":
        raise AgentExecutionError(
            f"LLM_PROVIDER={settings.llm_provider!r} nao suportado.",
            code="LLM_PROVIDER_UNSUPPORTED",
        )
    if not settings.gemini_api_key:
        raise AgentExecutionError("GEMINI_API_KEY nao configurada.", code="LLM_NOT_CONFIGURED")

    return ChatGoogleGenerativeAI(model=settings.llm_model, google_api_key=settings.gemini_api_key)


class LangGraphRunner:
    """`AgentRunner` implementation backed by LangGraph's prebuilt ReAct agent."""

    name = "langgraph"

    def __init__(self, *, settings: Settings, model: BaseChatModel | None = None) -> None:
        # `model` is injectable for the same reason it is on the Pydantic AI runner: the tests
        # drive the real graph with a fake chat model, so no credential or network is involved.
        self._settings = settings
        self._model = model if model is not None else _build_model(settings)

    async def stream(
        self,
        *,
        user_message: str,
        history: Sequence[HistoryMessage],
        deps: AgentDeps,
    ) -> AsyncIterator[AgentEvent]:
        state = _StreamState()
        emitted_anything = False

        try:
            # The same guardrails as the primary runner. Without them, switching
            # `AGENT_RUNNER=langgraph` would quietly drop the cost, latency and retry
            # behaviour — a comparison is only honest if both sides run under the same limits.
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                for attempt in range(1, self._settings.agent_max_attempts + 1):
                    try:
                        instructions, messages = await _build_prompt(user_message, history, deps)
                        agent = create_agent(
                            _apply_overrides(self._model, deps.overrides),
                            _build_tools(deps),
                            system_prompt=instructions,
                        )

                        # The graph declares its input as a TypedDict; mypy will not accept a
                        # dict literal against that overload, and the runtime contract is
                        # exactly this shape.
                        graph_input: Any = {"messages": messages}
                        # A ReAct step is one model call plus one tool batch, so the recursion
                        # limit is the closest equivalent to `tool_calls_limit`; the +2 covers
                        # the first model turn and the final answer.
                        config: Any = {"recursion_limit": deps.max_tool_calls * 2 + 2}

                        async for event in agent.astream_events(
                            graph_input, version="v2", config=config
                        ):
                            emitted = _translate(event, state, deps)
                            if emitted is not None:
                                emitted_anything = True
                                yield emitted
                        break

                    except Exception as error:
                        # A partially delivered stream cannot be retried: the client already has
                        # those tokens and a second run would duplicate them.
                        if (
                            emitted_anything
                            or attempt >= self._settings.agent_max_attempts
                            or not is_retryable(error)
                        ):
                            raise

                        delay = backoff_delay(attempt)
                        logger.warning(
                            "agent_run_retrying",
                            runner=self.name,
                            tutor_id=deps.tutor.id,
                            session_id=deps.session_id,
                            attempt=attempt,
                            delay_seconds=round(delay, 2),
                            error_type=type(error).__name__,
                        )
                        deps.invocations.clear()
                        deps.citations.clear()
                        state.answer.clear()
                        await asyncio.sleep(delay)

        except TimeoutError:
            logger.warning(
                "agent_timeout",
                runner=self.name,
                tutor_id=deps.tutor.id,
                session_id=deps.session_id,
                seconds=self._settings.agent_timeout_seconds,
            )
            yield AgentEvent(
                kind=AgentEventKind.ERROR,
                error_code=AgentTimeoutError.code,
                text=AgentTimeoutError.message,
            )
            return

        except Exception as exc:
            logger.exception(
                "agent_failed",
                runner=self.name,
                tutor_id=deps.tutor.id,
                error_type=type(exc).__name__,
            )
            yield AgentEvent(
                kind=AgentEventKind.ERROR,
                error_code=AgentExecutionError.code,
                text=AgentExecutionError.message,
            )
            return

        yield AgentEvent(
            kind=AgentEventKind.DONE,
            text="".join(state.answer),
            citations=tuple(deps.citations.values()),
            tool_calls=tuple(deps.invocations),
        )


def _apply_overrides(model: BaseChatModel, overrides: ModelOverrides) -> BaseChatModel:
    """Per-tutor model settings, the LangChain way.

    `bind` returns a configured copy rather than mutating the shared instance — the runner is
    built once per process and serves every tutor, so mutating it would leak one tutor's
    temperature into the next one's answer.
    """
    bound: dict[str, Any] = {}
    if overrides.temperature is not None:
        bound["temperature"] = overrides.temperature
    if overrides.max_output_tokens is not None:
        bound["max_output_tokens"] = overrides.max_output_tokens

    if not bound:
        return model
    # `bind` is typed as returning a Runnable; at run time it is still a chat model, which is
    # what `create_agent` requires.
    return model.bind(**bound)  # type: ignore[return-value]


def _build_tools(deps: AgentDeps) -> list[StructuredTool]:
    """Wrap the framework-agnostic tools.

    The bodies are the same functions the Pydantic AI runner uses; only the registration
    differs. `deps` is captured in the closure because LangGraph has no dependency injection
    equivalent to `RunContext`.
    """

    async def list_sources() -> str:
        """Lista as fontes de conhecimento configuradas para este tutor."""
        return (await knowledge_tools.list_sources(deps)).text

    async def get_source_outline(source_id: str) -> str:
        """Mostra os titulos das secoes de uma fonte."""
        return (await knowledge_tools.get_source_outline(deps, source_id)).text

    async def search_source(source_id: str, query: str, max_snippets: int = 3) -> str:
        """Procura trechos relevantes dentro de uma fonte por palavras-chave."""
        return (await knowledge_tools.search_source(deps, source_id, query, max_snippets)).text

    async def fetch_source(source_id: str, offset: int = 0, max_chars: int = 4_000) -> str:
        """Le o texto de uma fonte sequencialmente, em partes."""
        return (await knowledge_tools.fetch_source(deps, source_id, offset, max_chars)).text

    return [
        StructuredTool.from_function(coroutine=function, name=function.__name__)
        for function in (list_sources, get_source_outline, search_source, fetch_source)
    ]


async def _build_prompt(
    user_message: str, history: Sequence[HistoryMessage], deps: AgentDeps
) -> tuple[str, list[BaseMessage]]:
    """Instructions plus conversation, from the same builders the other runner uses."""
    catalogue = await deps.sources.list_sources(deps.tutor.id)
    instructions = f"{build_instructions(deps)}\n\n{format_source_catalogue(catalogue)}"

    messages: list[BaseMessage] = [
        HumanMessage(content=item.content)
        if item.role is ChatRole.USER
        else AIMessage(content=item.content)
        for item in history
    ]
    messages.append(HumanMessage(content=user_message))
    return instructions, messages


@dataclass(slots=True)
class _StreamState:
    """Accumulated answer, plus whether the current model turn produced deltas."""

    answer: list[str] = field(default_factory=list)
    streamed_this_turn: bool = False


def _translate(event: Mapping[str, Any], state: _StreamState, deps: AgentDeps) -> AgentEvent | None:
    """Map a LangChain stream event to the transport-agnostic one.

    Compare with the Pydantic AI runner: there the events are typed objects matched with
    `isinstance`, so a wrong field is a type error. Here they are `TypedDict`s keyed by a string
    name, and the payload shape depends on that string — the mapping is effectively
    stringly-typed and only fails at run time.
    """
    kind = event.get("event")

    if kind == "on_chat_model_start":
        state.streamed_this_turn = False
        return None

    if kind == "on_chat_model_stream":
        text = _content_of(event.get("data", {}).get("chunk"))
        if not text:
            return None
        state.streamed_this_turn = True
        state.answer.append(text)
        return AgentEvent(kind=AgentEventKind.TOKEN, text=text)

    if kind == "on_chat_model_end":
        # A model that does not stream emits no chunks at all, only this final event. Reading
        # the answer solely from deltas would return an empty response for it -- and the failure
        # would be invisible against a streaming model like Gemini.
        if state.streamed_this_turn:
            return None
        text = _content_of(event.get("data", {}).get("output"))
        if not text:
            return None
        state.answer.append(text)
        return AgentEvent(kind=AgentEventKind.TOKEN, text=text)

    if kind == "on_tool_start":
        return AgentEvent(
            kind=AgentEventKind.TOOL_STARTED,
            tool_name=event.get("name"),
            source_label=deps.label_for(_source_id_of(event.get("data", {}).get("input"))),
        )

    if kind == "on_tool_end":
        return AgentEvent(kind=AgentEventKind.TOOL_FINISHED, tool_name=event.get("name"))

    return None


def _content_of(payload: object) -> str:
    """Extract text from a message or chunk, ignoring tool-call-only turns."""
    content = getattr(payload, "content", None)
    if isinstance(content, str):
        return content
    # Multimodal models return a list of blocks; only the text ones matter here.
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _source_id_of(payload: object) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("source_id")
        return str(value) if value is not None else None
    return None
