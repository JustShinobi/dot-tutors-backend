"""Persistence-level guarantees of the schema."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat import MessageRole
from app.db.models.tutor import SourceKind, Tutor, TutorSource, TutorStatus


def _tutor(**overrides: object) -> Tutor:
    data: dict[str, object] = {
        "title": "Tutor",
        "slug": "tutor",
        "system_instructions": "Instrucoes.",
    }
    data.update(overrides)
    return Tutor(**data)  # type: ignore[arg-type]


async def test_enum_columns_round_trip_as_enum_members(session: AsyncSession) -> None:
    """Regression: a plain String column returns `str`, silently breaking `is` comparisons.

    `tutor.status is TutorStatus.ACTIVE` and the `is_active` property both depend on the value
    coming back as an enum member after a reload, not as the raw string.
    """
    tutor = _tutor(status=TutorStatus.INACTIVE)
    tutor.sources.append(TutorSource(kind=SourceKind.INLINE_TEXT, label="FAQ", content="texto"))
    session.add(tutor)
    await session.commit()
    session.expunge_all()

    reloaded = (await session.execute(select(Tutor).where(Tutor.slug == "tutor"))).scalar_one()

    assert reloaded.status is TutorStatus.INACTIVE
    assert reloaded.is_active is False
    assert reloaded.sources[0].kind is SourceKind.INLINE_TEXT


async def test_slug_is_unique(session: AsyncSession) -> None:
    session.add(_tutor())
    await session.commit()

    session.add(_tutor(title="Outro"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_invalid_enum_value_is_rejected_before_reaching_the_database(
    session: AsyncSession,
) -> None:
    """`validate_strings=True` turns a typo into a loud failure instead of a bad row."""
    session.add(_tutor(status="pendente"))

    with pytest.raises(StatementError, match="not among the defined enum values"):
        await session.commit()


async def test_message_role_values_are_stored_lowercase() -> None:
    # The API contract exposes the *value*, not the member name: "user", not "USER".
    assert [role.value for role in MessageRole] == ["user", "assistant", "system"]
