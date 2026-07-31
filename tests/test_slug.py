"""Slug generation rules."""

from __future__ import annotations

import pytest

from app.services.slug import generate_unique_slug, normalize_slug


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tutor de Matemática", "tutor-de-matematica"),
        ("  Onboarding   DOT  ", "onboarding-dot"),
        ("Tutor#1 (beta)!", "tutor-1-beta"),
        ("Ação & Reação", "acao-reacao"),
        ("---", ""),
    ],
)
def test_normalize_slug_folds_accents_and_punctuation(raw: str, expected: str) -> None:
    assert normalize_slug(raw) == expected


async def test_generate_unique_slug_returns_base_when_free() -> None:
    async def exists(_: str) -> bool:
        return False

    assert await generate_unique_slug("Tutor de Inglês", exists=exists) == "tutor-de-ingles"


async def test_generate_unique_slug_appends_suffix_on_collision() -> None:
    taken = {"tutor-de-ingles", "tutor-de-ingles-2"}

    async def exists(slug: str) -> bool:
        return slug in taken

    assert await generate_unique_slug("Tutor de Inglês", exists=exists) == "tutor-de-ingles-3"


async def test_generate_unique_slug_falls_back_when_title_has_no_usable_characters() -> None:
    async def exists(_: str) -> bool:
        return False

    assert await generate_unique_slug("!!!", exists=exists) == "tutor"


async def test_generate_unique_slug_gives_up_instead_of_looping_forever() -> None:
    async def exists(_: str) -> bool:
        return True

    with pytest.raises(RuntimeError, match="unique slug"):
        await generate_unique_slug("tutor", exists=exists, max_attempts=3)
