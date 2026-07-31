"""Shared test fixtures.

Tests run against a real SQLite database created in memory: the schema, constraints and
relationship cascades are exercised for real, without needing a PostgreSQL server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.repositories.tutor import SourceRepository, TutorRepository
from app.services.tutor_service import TutorService


@pytest.fixture
def settings() -> Settings:
    """Settings isolated from the developer's `.env`."""
    return Settings(
        app_env="test",
        debug=False,
        log_level="WARNING",
        log_format="json",
        # A shared in-memory database, so every connection of the pool sees the same schema.
        database_url="sqlite+aiosqlite:///file:test?mode=memory&cache=shared&uri=true",
        jwt_secret="test-secret-not-used-anywhere-real",
        gemini_api_key="",
    )


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = create_session_factory(engine)
    async with factory() as session:
        yield session


@pytest.fixture
def tutor_service(session: AsyncSession, settings: Settings) -> TutorService:
    return TutorService(
        tutors=TutorRepository(session),
        sources=SourceRepository(session),
        settings=settings,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as async_client,
        app.router.lifespan_context(app),
    ):
        yield async_client
