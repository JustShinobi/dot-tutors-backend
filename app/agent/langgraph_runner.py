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

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from app.agent import tools as knowledge_tools
from app.agent.contracts import (
    AgentDeps,
    AgentEvent,
    AgentEventKind,
    ChatRole,
    HistoryMessage,
)
from app.agent.prompts import build_instructions, format_source_catalogue
from app.core.config import Settings
from app.core.errors import AgentExecutionError
from app.core.logging import get_logger

logger = get_logger(__name__)


class LangGraphRunner:
    """`AgentRunner` implementation backed by LangGraph's prebuilt ReAct agent."""

    name = "langgraph"

    def __init__(self, *, settings: Settings) -> None:
        if settings.llm_provider != "google":
            raise AgentExecutionError(
                f"LLM_PROVIDER={settings.llm_provider!r} nao suportado.",
                code="LLM_PROVIDER_UNSUPPORTED",
            )
        if not settings.gemini_api_key:
            raise AgentExecutionError("GEMINI_API_KEY nao configurada.", code="LLM_NOT_CONFIGURED")

        self._settings = settings
        self._model = ChatGoogleGenerativeAI(
            model=settings.llm_model,
            google_api_key=settings.gemini_api_key,
        )

    async def stream(
        self,
        *,
        user_message: str,
        history: Sequence[HistoryMessage],
        deps: AgentDeps,
    ) -> AsyncIterator[AgentEvent]:
        answer: list[str] = []

        try:
            agent = create_react_agent(self._model, _build_tools(deps))
            messages = await _build_messages(user_message, history, deps)

            async for event in agent.astream_events({"messages": messages}, version="v2"):
                emitted = _translate(event, answer=answer)
                if emitted is not None:
                    yield emitted

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
            text="".join(answer),
            citations=tuple(deps.citations.values()),
            tool_calls=tuple(deps.invocations),
        )


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


async def _build_messages(
    user_message: str, history: Sequence[HistoryMessage], deps: AgentDeps
) -> list[BaseMessage]:
    catalogue = await deps.sources.list_sources(deps.tutor.id)
    instructions = f"{build_instructions(deps)}\n\n{format_source_catalogue(catalogue)}"

    messages: list[BaseMessage] = [SystemMessage(content=instructions)]
    for item in history:
        messages.append(
            HumanMessage(content=item.content)
            if item.role is ChatRole.USER
            else AIMessage(content=item.content)
        )
    messages.append(HumanMessage(content=user_message))
    return messages


def _translate(event: Mapping[str, Any], answer: list[str]) -> AgentEvent | None:
    """Map a LangChain stream event to the transport-agnostic one.

    Compare with the Pydantic AI runner: there the events are typed objects matched with
    `isinstance`, so a wrong field is a type error. Here they are `TypedDict`s keyed by a string
    name, and the payload shape depends on that string — the mapping is effectively
    stringly-typed and only fails at run time.
    """
    kind = event.get("event")

    if kind == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        text = getattr(chunk, "content", "") or ""
        if isinstance(text, str) and text:
            answer.append(text)
            return AgentEvent(kind=AgentEventKind.TOKEN, text=text)
        return None

    if kind == "on_tool_start":
        return AgentEvent(
            kind=AgentEventKind.TOOL_STARTED,
            tool_name=event.get("name"),
            source_label=_source_id_of(event.get("data", {}).get("input")),
        )

    if kind == "on_tool_end":
        return AgentEvent(kind=AgentEventKind.TOOL_FINISHED, tool_name=event.get("name"))

    return None


def _source_id_of(payload: object) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("source_id")
        return str(value) if value is not None else None
    return None
