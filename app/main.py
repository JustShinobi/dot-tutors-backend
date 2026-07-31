"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "application_started",
        app_env=settings.app_env,
        agent_runner=settings.agent_runner,
        llm_model=settings.llm_model,
        database="sqlite" if settings.is_sqlite else "postgresql",
    )
    yield
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

    app.include_router(health.router)

    return app


app = create_app()
