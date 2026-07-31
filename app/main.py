"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.agent.runner import build_runner
from app.api.cors import DualPolicyCORSMiddleware
from app.api.error_handlers import register_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.v1 import auth, embed, embed_keys, health, tutors
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    if not hasattr(app.state, "engine"):
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)

    # One outbound client for the whole process: connection reuse matters when the agent
    # fetches the same handful of source hosts on every conversation.
    app.state.http_client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=settings.source_fetch_timeout_seconds,
        headers={"User-Agent": "dot-tutors/0.1 (+knowledge-source-fetcher)"},
    )

    # Built once: constructing it creates the model client. A missing or invalid LLM
    # configuration must not stop the admin API from booting, so the failure is deferred to
    # the first chat request instead of crashing startup.
    try:
        app.state.agent_runner = build_runner(settings)
        runner_ready = True
    except AppError as exc:
        app.state.agent_runner = None
        runner_ready = False
        logger.warning("agent_runner_unavailable", error_code=exc.code, reason=exc.message)

    logger.info(
        "application_started",
        app_env=settings.app_env,
        agent_runner=settings.agent_runner,
        agent_ready=runner_ready,
        llm_model=settings.llm_model,
        database="sqlite" if settings.is_sqlite else "postgresql",
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.engine.dispose()
        logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_format == "json")

    app = FastAPI(
        title="DOT Tutors API",
        version="0.1.0",
        description=(
            "API de gestao de tutores e runtime de conversacao para widget incorporavel via iframe."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(DualPolicyCORSMiddleware, admin_origins=settings.admin_origins)

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(tutors.router)
    app.include_router(embed_keys.router)
    app.include_router(embed.router)

    return app


app = create_app()
