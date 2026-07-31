"""Retry policy for model calls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic_ai import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.contracts import AgentDeps, AgentEventKind
from app.agent.pydantic_ai_runner import PydanticAIRunner
from app.agent.retry import backoff_delay, is_retryable, with_retry
from app.core.config import Settings
from app.schemas.tutor import TutorCreate
from app.services.source_service import SourceService
from app.services.tutor_service import TutorService

# --- classification --------------------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(status: int) -> None:
    error = httpx.HTTPStatusError(
        "falhou",
        request=httpx.Request("POST", "https://api.exemplo/v1"),
        response=httpx.Response(status),
    )

    assert is_retryable(error)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_mistakes_are_not_retried(status: int) -> None:
    """A malformed request or a bad key fails identically on the next try."""
    error = httpx.HTTPStatusError(
        "falhou",
        request=httpx.Request("POST", "https://api.exemplo/v1"),
        response=httpx.Response(status),
    )

    assert not is_retryable(error)


def test_network_failures_are_retryable() -> None:
    assert is_retryable(httpx.ConnectTimeout("timeout"))
    assert is_retryable(httpx.ReadError("conexao caiu"))


def test_a_status_wrapped_by_an_sdk_is_still_found() -> None:
    """SDKs wrap HTTP errors in their own classes; the shape is what gets inspected."""

    class ProviderError(Exception):
        status_code = 503

    assert is_retryable(ProviderError("indisponivel"))


def test_a_status_hidden_in_the_cause_chain_is_found() -> None:
    inner = httpx.HTTPStatusError(
        "falhou",
        request=httpx.Request("POST", "https://api.exemplo/v1"),
        response=httpx.Response(429),
    )
    outer = RuntimeError("falha do agente")
    outer.__cause__ = inner

    assert is_retryable(outer)


def test_an_unrecognised_error_is_not_retried() -> None:
    assert not is_retryable(ValueError("erro de programacao"))


# --- backoff ---------------------------------------------------------------


def test_backoff_grows_exponentially_and_is_capped() -> None:
    # Jitter pinned to its maximum so the growth curve itself is under test.
    delays = [backoff_delay(attempt, jitter=lambda: 1.0) for attempt in range(1, 8)]

    assert delays[0] == pytest.approx(0.5)
    assert delays[1] == pytest.approx(1.0)
    assert delays[2] == pytest.approx(2.0)
    assert max(delays) <= 8.0
    assert delays == sorted(delays)


def test_jitter_spreads_retries() -> None:
    """Without jitter, everyone who failed together retries together."""
    assert backoff_delay(3, jitter=lambda: 0.0) == 0.0
    assert backoff_delay(3, jitter=lambda: 1.0) == pytest.approx(2.0)


# --- the helper ------------------------------------------------------------


async def test_with_retry_returns_after_a_transient_failure() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectTimeout("timeout")
        return "ok"

    result = await with_retry(flaky, max_attempts=3, on_retry=lambda *_: None)

    assert result == "ok"
    assert attempts == 3


async def test_with_retry_gives_up_and_reraises() -> None:
    async def always_fails() -> str:
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(httpx.ConnectTimeout):
        await with_retry(always_fails, max_attempts=2, on_retry=lambda *_: None)


async def test_with_retry_does_not_repeat_a_permanent_failure() -> None:
    attempts = 0

    async def bad_request() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("erro de programacao")

    with pytest.raises(ValueError):
        await with_retry(bad_request, max_attempts=5, on_retry=lambda *_: None)

    assert attempts == 1


# --- inside the runner -----------------------------------------------------


async def _make_deps(
    tutor_service: TutorService, session: AsyncSession, settings: Settings
) -> AgentDeps:
    tutor = await tutor_service.create(
        TutorCreate(title="Tutor", system_instructions="Instrucoes suficientes.")
    )
    async with httpx.AsyncClient() as client:
        return AgentDeps(
            tutor=tutor,
            sources=SourceService(session=session, settings=settings, http_client=client),
            session_id="sessao-retry",
            max_tool_calls=6,
        )


async def test_the_runner_recovers_from_a_transient_failure(
    tutor_service: TutorService, session: AsyncSession, settings: Settings
) -> None:
    calls = 0

    async def flaky(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectTimeout("provedor instavel")
        yield "Resposta apos a retentativa."

    settings.agent_max_attempts = 3
    deps = await _make_deps(tutor_service, session, settings)
    runner = PydanticAIRunner(settings=settings, model=FunctionModel(stream_function=flaky))

    events = [event async for event in runner.stream(user_message="oi", history=[], deps=deps)]

    assert events[-1].kind is AgentEventKind.DONE
    assert events[-1].text == "Resposta apos a retentativa."
    assert calls == 2


async def test_a_partially_streamed_answer_is_never_retried(
    tutor_service: TutorService, session: AsyncSession, settings: Settings
) -> None:
    """The client already holds those tokens; running again would duplicate them."""
    calls = 0

    async def fails_midway(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        yield "Comecei a responder"
        raise httpx.ConnectTimeout("caiu no meio")

    settings.agent_max_attempts = 3
    deps = await _make_deps(tutor_service, session, settings)
    runner = PydanticAIRunner(settings=settings, model=FunctionModel(stream_function=fails_midway))

    events = [event async for event in runner.stream(user_message="oi", history=[], deps=deps)]

    assert calls == 1
    assert events[-1].kind is AgentEventKind.ERROR
    # The partial text still reached the client, and is not repeated.
    assert any(event.kind is AgentEventKind.TOKEN for event in events)


async def test_a_permanent_failure_is_not_retried_by_the_runner(
    tutor_service: TutorService, session: AsyncSession, settings: Settings
) -> None:
    calls = 0

    async def broken(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        raise ValueError("erro de programacao")
        yield ""  # pragma: no cover

    settings.agent_max_attempts = 3
    deps = await _make_deps(tutor_service, session, settings)
    runner = PydanticAIRunner(settings=settings, model=FunctionModel(stream_function=broken))

    events = [event async for event in runner.stream(user_message="oi", history=[], deps=deps)]

    assert calls == 1
    assert events[-1].kind is AgentEventKind.ERROR


async def test_retries_stay_inside_the_total_timeout(
    tutor_service: TutorService, session: AsyncSession, settings: Settings
) -> None:
    """Retrying must never extend the deadline the user is waiting against."""
    import asyncio

    async def slow_and_flaky(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        await asyncio.sleep(5)
        yield "tarde demais"

    settings.agent_max_attempts = 3
    settings.agent_timeout_seconds = 1
    deps = await _make_deps(tutor_service, session, settings)
    runner = PydanticAIRunner(
        settings=settings, model=FunctionModel(stream_function=slow_and_flaky)
    )

    events: list[Any] = [
        event async for event in runner.stream(user_message="oi", history=[], deps=deps)
    ]

    assert events[-1].kind is AgentEventKind.ERROR
    assert events[-1].error_code == "AGENT_TIMEOUT"
