"""Declarative base and column types shared by every model.

The schema has to run on both SQLite (default, zero-setup) and PostgreSQL (production parity),
so it avoids dialect-specific types: enums are stored as strings validated in Python, and
structured columns use the portable `JSON` type instead of PostgreSQL's `JSONB`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


def str_enum_column(enum_class: type[StrEnum]) -> SAEnum:
    """Portable column type for a `StrEnum`.

    A plain `String` column would round-trip as `str`, so `tutor.status is TutorStatus.ACTIVE`
    would silently be false after a reload. This stores the member *value* (not its name) in a
    VARCHAR with a CHECK constraint — identical on SQLite and PostgreSQL, and free of the
    migration pain of PostgreSQL's native enums.
    """
    return SAEnum(
        enum_class,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def utcnow() -> datetime:
    """Timezone-aware "now"."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC, on every dialect.

    PostgreSQL round-trips `timestamptz` with its offset intact, but **SQLite has no timezone
    type and gives the value back naive**. Comparing that with an aware `utcnow()` raises
    `TypeError: can't compare offset-naive and offset-aware datetimes` — a failure that appears
    only on the default local database, which is exactly where it does the most damage.

    Normalising in the column type fixes it once for every timestamp, instead of scattering
    defensive conversions through the services.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # A naive value reaching the database is a bug upstream; assume UTC rather than
            # silently storing local time.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    # Structured columns use the portable `JSON` type, which maps to PostgreSQL's `JSON` and to
    # SQLite's text-with-serialisation, keeping a single schema for both dialects.
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSON,
        list[str]: JSON,
        list[dict[str, Any]]: JSON,
        datetime: UtcDateTime,
    }


class UUIDPrimaryKeyMixin:
    """String UUID primary key.

    Stored as `str` rather than a native UUID column: SQLite has no UUID type, and keeping one
    representation avoids a class of bugs where the same id compares unequal across dialects.
    """

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
