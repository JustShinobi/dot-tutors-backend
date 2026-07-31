"""The comparative LangGraph runner (decision D1).

The README argues the Pydantic AI choice by comparing the two implementations. An argument
backed by code that nothing exercises is still an argument from assertion, so this module puts
the alternative runner under the same kind of test as the primary one: the real graph, the real
tools, the real BM25 retrieval — only the chat model is fake.

Writing it also *is* the evidence for one of the README's claims. Compare the fake below with
`_scripted_model` in `test_agent.py`: there, scripting a tool call is one `DeltaToolCall`; here
it takes a chat model subclass that reimplements `bind_tools` and `_generate`. That difference
is the point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.contracts import AgentDeps, AgentEvent, AgentEventKind
from app.core.config import Settings
from app.db.models.tutor import SourceKind
from app.schemas.tutor import SourceCreate, TutorCreate
from app.services.source_service import SourceService
from app.services.tutor_service import TutorService

pytest.importorskip("langgraph", reason="extra opcional [langgraph] nao instalado")

from app.agent.langgraph_runner import LangGraphRunner

POLICY = """\
# Politica de Trabalho Remoto

## Auxilio home office
O auxilio e de R$ 150,00 por mes, pago junto ao salario.

## Ferias
As ferias seguem a CLT: 30 dias por periodo aquisitivo.
"""


class ScriptedChatModel(BaseChatModel):
    """A chat model that emits a fixed sequence of turns.

    Each call consumes one entry: a `(tool_name, args)` tuple becomes a tool call, a string
    becomes the final answer. `bind_tools` is overridden to a no-op because the base
    implementation raises, and the graph binds tools before the first call.
    """

    turns: list[Any] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, BaseMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        turn = self.turns[self.calls] if self.calls < len(self.turns) else "Resposta final."
        self.calls += 1

        if isinstance(turn, tuple):
            name, args = turn
            message = AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": f"call-{self.calls}"}],
            )
        else:
            message = AIMessage(content=str(turn))

        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def source_service(
    session: AsyncSession, settings: Settings, http_client: httpx.AsyncClient
) -> SourceService:
    return SourceService(session=session, settings=settings, http_client=http_client)


async def _make_deps(tutors: TutorService, sources: SourceService) -> AgentDeps:
    tutor = await tutors.create(
        TutorCreate(
            title="Tutor de Politicas",
            system_instructions="Responda com base nas fontes configuradas.",
            sources=[
                SourceCreate(
                    kind=SourceKind.INLINE_TEXT,
                    label="Politica de trabalho remoto",
                    content=POLICY,
                )
            ],
        )
    )
    return AgentDeps(tutor=tutor, sources=sources, session_id="sessao-langgraph", max_tool_calls=6)


async def _collect(runner: LangGraphRunner, deps: AgentDeps, message: str) -> list[AgentEvent]:
    return [event async for event in runner.stream(user_message=message, history=[], deps=deps)]


# --- the contract ----------------------------------------------------------


async def test_the_alternative_runner_satisfies_the_same_contract(
    tutor_service: TutorService, source_service: SourceService, settings: Settings
) -> None:
    """Both runners are interchangeable behind `AgentRunner` — that is what D1 rests on."""
    from app.agent.contracts import AgentRunner

    runner = LangGraphRunner(settings=settings, model=ScriptedChatModel())

    assert isinstance(runner, AgentRunner)
    assert runner.name == "langgraph"


async def test_it_searches_the_source_and_answers(
    tutor_service: TutorService, source_service: SourceService, settings: Settings
) -> None:
    deps = await _make_deps(tutor_service, source_service)
    source_id = deps.tutor.sources[0].id

    runner = LangGraphRunner(
        settings=settings,
        model=ScriptedChatModel(
            turns=[
                ("search_source", {"source_id": source_id, "query": "auxilio home office"}),
                "O auxilio home office e de R$ 150,00 por mes.",
            ]
        ),
    )

    events = await _collect(runner, deps, "Qual o valor do auxilio home office?")
    done = events[-1]

    assert done.kind is AgentEventKind.DONE
    assert "150,00" in done.text
    assert [call.name for call in done.tool_calls] == ["search_source"]
    assert [citation.label for citation in done.citations] == ["Politica de trabalho remoto"]


async def test_tool_events_reach_the_transport(
    tutor_service: TutorService, source_service: SourceService, settings: Settings
) -> None:
    deps = await _make_deps(tutor_service, source_service)
    source_id = deps.tutor.sources[0].id

    runner = LangGraphRunner(
        settings=settings,
        model=ScriptedChatModel(
            turns=[("search_source", {"source_id": source_id, "query": "ferias"}), "Pronto."]
        ),
    )

    kinds = [event.kind for event in await _collect(runner, deps, "ferias?")]

    assert AgentEventKind.TOOL_STARTED in kinds
    assert AgentEventKind.TOOL_FINISHED in kinds
    assert kinds.index(AgentEventKind.TOOL_STARTED) < kinds.index(AgentEventKind.DONE)


async def test_the_same_tools_are_reused_untouched(
    tutor_service: TutorService, source_service: SourceService, settings: Settings
) -> None:
    """The knowledge layer is shared: only the wiring differs between the two runners."""
    deps = await _make_deps(tutor_service, source_service)
    source_id = deps.tutor.sources[0].id

    runner = LangGraphRunner(
        settings=settings,
        model=ScriptedChatModel(
            turns=[
                ("list_sources", {}),
                ("get_source_outline", {"source_id": source_id}),
                ("fetch_source", {"source_id": source_id, "offset": 0, "max_chars": 500}),
                "Consultei o documento.",
            ]
        ),
    )

    done = (await _collect(runner, deps, "me mostre o documento"))[-1]

    assert [call.name for call in done.tool_calls] == [
        "list_sources",
        "get_source_outline",
        "fetch_source",
    ]
    assert all(call.ok for call in done.tool_calls)


async def test_a_hallucinated_source_id_does_not_break_the_run(
    tutor_service: TutorService, source_service: SourceService, settings: Settings
) -> None:
    deps = await _make_deps(tutor_service, source_service)

    runner = LangGraphRunner(
        settings=settings,
        model=ScriptedChatModel(
            turns=[
                ("search_source", {"source_id": "nao-existe", "query": "ferias"}),
                "Nao encontrei essa informacao.",
            ]
        ),
    )

    done = (await _collect(runner, deps, "ferias?"))[-1]

    assert done.kind is AgentEventKind.DONE
    assert done.tool_calls[0].ok is False
    assert done.citations == ()


async def test_a_model_failure_becomes_an_error_event(
    tutor_service: TutorService, source_service: SourceService, settings: Settings
) -> None:
    """Same guarantee as the primary runner: the transport never sees a raw exception."""

    class ExplodingModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
            msg = "modelo indisponivel"
            raise RuntimeError(msg)

    deps = await _make_deps(tutor_service, source_service)
    runner = LangGraphRunner(settings=settings, model=ExplodingModel())

    events = await _collect(runner, deps, "oi")

    assert events[-1].kind is AgentEventKind.ERROR
    assert events[-1].error_code == "AGENT_FAILED"
    assert "modelo indisponivel" not in events[-1].text


async def test_building_without_a_model_requires_credentials(settings: Settings) -> None:
    from app.core.errors import AgentExecutionError

    settings.gemini_api_key = ""

    with pytest.raises(AgentExecutionError, match="GEMINI_API_KEY"):
        LangGraphRunner(settings=settings)
