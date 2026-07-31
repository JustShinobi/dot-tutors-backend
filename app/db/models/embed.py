"""Embed keys: how a third-party site is authorised to load a tutor (PRD 3.4).

An embed key is **public by design** — it ships inside the `src` of the integrator's `<iframe>`
and is readable by anyone viewing that page. What actually protects the tutor is the pairing of
the key with an `Origin` allowlist, plus a short-lived session token and rate limiting.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.tutor import Tutor


class EmbedKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "embed_keys"

    tutor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tutors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)

    # Exact origins ("https://cliente.com"), compared scheme+host+port. An empty list means
    # "any origin" and is only acceptable in local development.
    allowed_origins: Mapped[list[str]] = mapped_column(default=list, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tutor: Mapped[Tutor] = relationship(back_populates="embed_keys")

    @property
    def allows_any_origin(self) -> bool:
        return not self.allowed_origins

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EmbedKey {self.public_key!r} active={self.is_active}>"
