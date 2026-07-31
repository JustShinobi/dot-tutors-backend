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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


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
    """Timezone-aware "now". SQLite has no native timestamptz, so UTC is enforced here."""
    return datetime.now(UTC)


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
    }


class UUIDPrimaryKeyMixin:
    """String UUID primary key.

    Stored as `str` rather than a native UUID column: SQLite has no UUID type, and keeping one
    representation avoids a class of bugs where the same id compares unequal across dialects.
    """

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
