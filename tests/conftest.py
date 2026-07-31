"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Settings isolated from the developer's `.env`."""
    return Settings(
        app_env="test",
        debug=False,
        log_level="WARNING",
        log_format="json",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-not-used-anywhere-real",
        gemini_api_key="",
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
