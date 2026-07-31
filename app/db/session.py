"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine for the configured database.

    SQLite gets `check_same_thread=False` because the async driver hands connections between
    threads; PostgreSQL gets connection pre-ping so a recycled connection does not surface as a
    request error.
    """
    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {"echo": False, "future": True}

    if settings.is_sqlite:
        connect_args["check_same_thread"] = False
    else:
        engine_kwargs |= {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}

    engine = create_async_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)

    if settings.is_sqlite:
        _enforce_sqlite_foreign_keys(engine)

    return engine


def _enforce_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Turn on foreign key enforcement for every SQLite connection.

    **SQLite ignores foreign keys unless asked**, per connection, and the default is off. Without
    this every `ondelete="CASCADE"` in the schema is decorative on the default database: deleting
    a tutor or an expired session would leave its messages behind as orphans — that is, the
    conversation content would survive the retention routine that claims to remove it.

    PostgreSQL enforces constraints natively, so this is the change that makes the two dialects
    actually behave alike, rather than only appearing to.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, rolling back if the caller raises."""
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
