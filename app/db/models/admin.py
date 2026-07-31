"""Administrative user (PRD 4.1.2).

The MVP has a single role. A table is used instead of a hardcoded environment user so that
password rotation and additional admins do not require a redeploy, and so the JWT carries a real
subject id.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_column


class AdminRole(StrEnum):
    ADMIN = "admin"


class AdminUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[AdminRole] = mapped_column(
        str_enum_column(AdminRole), default=AdminRole.ADMIN, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AdminUser {self.email!r}>"
