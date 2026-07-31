"""Cached text of a knowledge source.

Refetching every source on every message would make the tutor slow and hammer third-party
servers, so the extracted text is stored with a TTL and revalidated with `ETag` /
`Last-Modified` when it expires.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

if TYPE_CHECKING:
    from app.db.models.tutor import TutorSource


class SourceDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_documents"

    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tutor_sources.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # [{"heading": "...", "level": 2, "start": 0, "preview": "..."}] - lets the agent navigate
    # the document before deciding what to read.
    outline: Mapped[list[dict[str, Any]]] = mapped_column(default=list, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Last failure, kept so the agent can tell the user "this source is unavailable" instead of
    # silently answering without it.
    fetch_error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source: Mapped[TutorSource] = relationship(back_populates="document")

    def is_fresh(self, *, now: datetime | None = None) -> bool:
        return (now or utcnow()) < self.expires_at

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SourceDocument source={self.source_id} bytes={self.byte_size}>"
