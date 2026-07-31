"""Smoke tests: the application boots and answers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import create_session_factory
from app.main import create_app


async def test_healthz_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "dot-tutors-backend",
        "version": "0.1.0",
    }


async def test_readyz_reports_the_dependencies(client: AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_readyz_reports_an_unconfigured_agent_without_failing(
    client: AsyncClient, app: FastAPI
) -> None:
    """No model credential is a degraded product, not a dead instance: the panel still works."""
    app.state.agent_runner = None

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["agent"] == "unavailable"


async def test_openapi_schema_is_served(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "DOT Tutors API"


async def test_the_schema_is_hidden_once_deployed(
    settings: Settings, engine: AsyncEngine, agent_model: Any
) -> None:
    """The schema enumerates every admin route and payload — free reconnaissance in production."""
    from app.agent.pydantic_ai_runner import PydanticAIRunner

    deployed = settings.model_copy(update={"app_env": "staging"})
    application = create_app(deployed)
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)

    async with application.router.lifespan_context(application):
        application.state.agent_runner = PydanticAIRunner(settings=deployed, model=agent_model)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as probe:
            assert (await probe.get("/openapi.json")).status_code == 404
            assert (await probe.get("/docs")).status_code == 404
            # The probes still have to answer, or the deployment cannot be health-checked.
            assert (await probe.get("/healthz")).status_code == 200


async def test_the_schema_can_be_published_explicitly(
    settings: Settings, engine: AsyncEngine, agent_model: Any
) -> None:
    """A demo that wants the interactive docs opts in."""
    from app.agent.pydantic_ai_runner import PydanticAIRunner

    deployed = settings.model_copy(update={"app_env": "staging", "expose_api_docs": True})
    application = create_app(deployed)
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)

    async with application.router.lifespan_context(application):
        application.state.agent_runner = PydanticAIRunner(settings=deployed, model=agent_model)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as probe:
            assert (await probe.get("/openapi.json")).status_code == 200
