"""Tutor and its knowledge sources (PRD 4.1.1)."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_column

if TYPE_CHECKING:
    from app.db.models.embed import EmbedKey
    from app.db.models.source_cache import SourceDocument


class TutorStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class SourceKind(StrEnum):
    """How the runtime obtains the source content."""

    URL = "url"
    """Fetched over HTTP at conversation time, with cache, size and timeout limits."""

    INLINE_TEXT = "inline_text"
    """Pasted directly into the tutor configuration; no network access needed."""


class Tutor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tutors"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(String(280), default="", nullable=False)

    system_instructions: Mapped[str] = mapped_column(Text, nullable=False)
    greeting: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[TutorStatus] = mapped_column(
        str_enum_column(TutorStatus), default=TutorStatus.ACTIVE, nullable=False, index=True
    )

    # {"model": "...", "temperature": 0.3, "max_tool_calls": 6}. Overrides the global defaults
    # per tutor; kept as JSON so adding a knob does not require a migration.
    model_settings: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)

    sources: Mapped[list[TutorSource]] = relationship(
        back_populates="tutor",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TutorSource.created_at",
    )
    embed_keys: Mapped[list[EmbedKey]] = relationship(
        back_populates="tutor",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EmbedKey.created_at",
    )

    @property
    def is_active(self) -> bool:
        return self.status is TutorStatus.ACTIVE

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tutor {self.slug!r} status={self.status}>"


class TutorSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A knowledge source the agent may consult through its tools.

    Deliberately *not* an embedding or a vector index (PRD 6.2): the source is plain text the
    agent searches lexically and reads on demand.
    """

    __tablename__ = "tutor_sources"

    tutor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tutors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SourceKind] = mapped_column(str_enum_column(SourceKind), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)

    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    max_bytes: Mapped[int] = mapped_column(Integer, default=512_000, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tutor: Mapped[Tutor] = relationship(back_populates="sources")
    document: Mapped[SourceDocument | None] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TutorSource {self.label!r} kind={self.kind}>"
