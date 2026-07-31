"""Shared test fixtures.

Tests run against a real SQLite database created in memory: the schema, constraints and
relationship cascades are exercised for real, without needing a PostgreSQL server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models.admin import AdminRole, AdminUser
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.repositories.tutor import SourceRepository, TutorRepository
from app.services.tutor_service import TutorService

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "senha-de-teste-123"


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
        admin_origin="http://localhost:3000",
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
async def admin_user(session: AsyncSession) -> AdminUser:
    admin = AdminUser(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        role=AdminRole.ADMIN,
    )
    session.add(admin)
    await session.commit()
    return admin


@pytest.fixture
async def app(settings: Settings, engine: AsyncEngine) -> FastAPI:
    """App wired to the *same* engine as the `session` fixture.

    Without this, the test would set up data on one engine and the request would read from
    another, and every assertion about persisted state would be meaningless.
    """
    application = create_app(settings)
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.fixture
async def admin_token(client: AsyncClient, admin_user: AdminUser) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


@pytest.fixture
def auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}
