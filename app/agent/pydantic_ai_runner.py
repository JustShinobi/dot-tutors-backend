"""Agent implementation on Pydantic AI (decision D1).

This is the only module in the project that imports `pydantic_ai`.

Why this framework, in short: the PRD forbids vector RAG, which is exactly the layer where
LangChain adds most of its value; what remains is a typed tool-calling loop, which Pydantic AI
expresses with less machinery. Its `TestModel`/`FunctionModel` also let the agent be tested
without calling the LLM at all, which is what makes the testing requirement (PRD 5.3) cheap and
deterministic. The full argument lives in the README.

Streaming uses `Agent.run_stream_events`, which delivers text deltas *and* tool events on one
stream — so the widget can show "consulting <source>…" while the answer is still forming.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    UsageLimits,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from app.agent import tools
from app.agent.contracts import (
    AgentDeps,
    AgentEvent,
    AgentEventKind,
    ChatRole,
    HistoryMessage,
)
from app.agent.prompts import build_instructions, format_source_catalogue
from app.core.config import Settings
from app.core.errors import AgentExecutionError, AgentTimeoutError
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_model(settings: Settings) -> Model:
    """Resolve the configured LLM. Only Google/Gemini is wired for the MVP."""
    if settings.llm_provider != "google":
        msg = (
            f"LLM_PROVIDER={settings.llm_provider!r} nao suportado neste MVP. "
            "Use 'google' (Gemini)."
        )
        raise AgentExecutionError(msg, code="LLM_PROVIDER_UNSUPPORTED")

    if not settings.gemini_api_key:
        raise AgentExecutionError(
            "GEMINI_API_KEY nao configurada: o tutor nao consegue gerar respostas.",
            code="LLM_NOT_CONFIGURED",
        )

    return GoogleModel(
        settings.llm_model,
        provider=GoogleProvider(api_key=settings.gemini_api_key),
    )


def build_agent(model: Model | None) -> Agent[AgentDeps, str]:
    """Assemble the agent and register the knowledge tools.

    Tools are thin adapters: they call the plain functions in `app.agent.tools`, which know
    nothing about any framework, and record the invocation on `deps` so the transport layer can
    report what was consulted.
    """
    agent: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        output_type=str,
        name="dot-tutor",
        instructions=_instructions,
    )

    @agent.tool
    async def list_sources(ctx: RunContext[AgentDeps]) -> str:
        """Lista as fontes de conhecimento configuradas para este tutor."""
        return (await tools.list_sources(ctx.deps)).text

    @agent.tool
    async def get_source_outline(ctx: RunContext[AgentDeps], source_id: str) -> str:
        """Mostra os titulos das secoes de uma fonte, para localizar o assunto certo.

        Args:
            source_id: Identificador da fonte, obtido em list_sources.
        """
        return (await tools.get_source_outline(ctx.deps, source_id)).text

    @agent.tool
    async def search_source(
        ctx: RunContext[AgentDeps], source_id: str, query: str, max_snippets: int = 3
    ) -> str:
        """Procura trechos relevantes dentro de uma fonte por palavras-chave.

        Args:
            source_id: Identificador da fonte, obtido em list_sources.
            query: Palavras-chave que provavelmente aparecem no documento.
            max_snippets: Quantidade maxima de trechos a devolver (1 a 5).
        """
        return (await tools.search_source(ctx.deps, source_id, query, max_snippets)).text

    @agent.tool
    async def fetch_source(
        ctx: RunContext[AgentDeps], source_id: str, offset: int = 0, max_chars: int = 4_000
    ) -> str:
        """Le o texto de uma fonte sequencialmente, em partes.

        Args:
            source_id: Identificador da fonte, obtido em list_sources.
            offset: Posicao inicial da leitura, em caracteres.
            max_chars: Quantidade maxima de caracteres a devolver.
        """
        return (await tools.fetch_source(ctx.deps, source_id, offset, max_chars)).text

    return agent


async def _instructions(ctx: RunContext[AgentDeps]) -> str:
    """Dynamic instructions: tutor configuration plus the live source catalogue."""
    catalogue = await ctx.deps.sources.list_sources(ctx.deps.tutor.id)
    return f"{build_instructions(ctx.deps)}\n\n{format_source_catalogue(catalogue)}"


class PydanticAIRunner:
    """`AgentRunner` implementation backed by Pydantic AI."""

    name = "pydantic_ai"

    def __init__(self, *, settings: Settings, model: Model | None = None) -> None:
        self._settings = settings
        self._model = model if model is not None else build_model(settings)
        self._agent = build_agent(self._model)

    async def stream(
        self,
        *,
        user_message: str,
        history: Sequence[HistoryMessage],
        deps: AgentDeps,
    ) -> AsyncIterator[AgentEvent]:
        limits = UsageLimits(tool_calls_limit=deps.max_tool_calls)
        answer: list[str] = []
        usage: dict[str, Any] = {}

        try:
            async with asyncio.timeout(self._settings.agent_timeout_seconds):
                async with self._agent.run_stream_events(
                    user_message,
                    message_history=_to_model_messages(history),
                    deps=deps,
                    usage_limits=limits,
                ) as events:
                    async for event in events:
                        emitted = _translate(event, answer=answer, usage=usage)
                        if emitted is not None:
                            yield emitted

        except TimeoutError:
            logger.warning(
                "agent_timeout",
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
                tutor_id=deps.tutor.id,
                session_id=deps.session_id,
                error_type=type(exc).__name__,
            )
            yield AgentEvent(
                kind=AgentEventKind.ERROR,
                error_code=AgentExecutionError.code,
                text=AgentExecutionError.message,
            )
            return

        logger.info(
            "agent_run_finished",
            tutor_id=deps.tutor.id,
            session_id=deps.session_id,
            tool_calls=len(deps.invocations),
            answer_chars=sum(len(part) for part in answer),
        )

        yield AgentEvent(
            kind=AgentEventKind.DONE,
            text="".join(answer),
            citations=tuple(deps.citations.values()),
            tool_calls=tuple(deps.invocations),
            usage=usage,
        )


def _translate(event: object, *, answer: list[str], usage: dict[str, Any]) -> AgentEvent | None:
    """Map a Pydantic AI stream event to our transport-agnostic event."""
    # The first slice of text arrives as PartStartEvent, and only the *continuations* come as
    # PartDeltaEvent. Handling deltas alone silently drops the opening of every answer.
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return _emit_text(event.part.content, answer=answer)

    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return _emit_text(event.delta.content_delta, answer=answer)

    if isinstance(event, FunctionToolCallEvent):
        return AgentEvent(
            kind=AgentEventKind.TOOL_STARTED,
            tool_name=event.part.tool_name,
            source_label=_source_id_of(event.part),
        )

    if isinstance(event, FunctionToolResultEvent):
        return AgentEvent(kind=AgentEventKind.TOOL_FINISHED, tool_name=event.part.tool_name)

    if isinstance(event, AgentRunResultEvent):
        run_usage = event.result.usage
        usage.update(
            {
                "input_tokens": run_usage.input_tokens,
                "output_tokens": run_usage.output_tokens,
                "requests": run_usage.requests,
                "tool_calls": run_usage.tool_calls,
            }
        )
        return None

    return None


def _emit_text(text: str, *, answer: list[str]) -> AgentEvent | None:
    if not text:
        return None
    answer.append(text)
    return AgentEvent(kind=AgentEventKind.TOKEN, text=text)


def _source_id_of(part: ToolCallPart) -> str | None:
    """Extract `source_id` from a tool call, whether the model sent JSON or a dict.

    This is only a hint shown while the call is in flight; the authoritative record of what was
    consulted comes from `deps.invocations` at the end of the run.
    """
    try:
        args = part.args_as_dict()
    except (ValueError, TypeError):
        return None
    value = args.get("source_id")
    return str(value) if value is not None else None


def _to_model_messages(history: Sequence[HistoryMessage]) -> list[ModelMessage]:
    """Convert stored history into the framework's message objects."""
    messages: list[ModelMessage] = []
    for item in history:
        if item.role is ChatRole.USER:
            messages.append(ModelRequest(parts=[UserPromptPart(content=item.content)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=item.content)]))
    return messages
