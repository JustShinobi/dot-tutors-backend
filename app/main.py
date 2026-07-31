"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.api.v1 import auth, health, tutors
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    logger.info(
        "application_started",
        app_env=settings.app_env,
        agent_runner=settings.agent_runner,
        llm_model=settings.llm_model,
        database="sqlite" if settings.is_sqlite else "postgresql",
    )
    try:
        yield
    finally:
        await engine.dispose()
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

    # The management API is called from the admin origin only. The embed routes need a
    # per-key allowlist that this static middleware cannot express, so they get their own
    # handling instead of being covered here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.admin_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(tutors.router)

    return app


app = create_app()
