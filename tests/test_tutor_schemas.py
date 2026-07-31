"""Input validation of the tutor schemas.

These rules are the first line of defence of the admin API, so they are asserted directly
instead of only through the HTTP layer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.tutor import SourceKind
from app.schemas.tutor import SourceCreate, TutorCreate


def test_url_source_requires_a_url() -> None:
    with pytest.raises(ValidationError, match="exige o campo 'url'"):
        SourceCreate(kind=SourceKind.URL, label="Guia")


def test_url_source_rejects_inline_content() -> None:
    with pytest.raises(ValidationError, match="nao aceita o campo 'content'"):
        SourceCreate(
            kind=SourceKind.URL,
            label="Guia",
            url="https://example.com/a.md",  # type: ignore[arg-type]
            content="texto",
        )


def test_inline_source_requires_non_blank_content() -> None:
    with pytest.raises(ValidationError, match="exige o campo 'content'"):
        SourceCreate(kind=SourceKind.INLINE_TEXT, label="FAQ", content="   ")


def test_source_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        SourceCreate(
            kind=SourceKind.URL,
            label="Local",
            url="file:///etc/passwd",  # type: ignore[arg-type]
        )


def test_tutor_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TutorCreate.model_validate(
            {
                "title": "Tutor",
                "system_instructions": "Instrucoes suficientes.",
                "unexpected": "campo",
            }
        )


def test_tutor_rejects_instructions_that_are_too_short() -> None:
    with pytest.raises(ValidationError):
        TutorCreate.model_validate({"title": "Tutor", "system_instructions": "curto"})


def test_tutor_normalizes_an_explicit_slug() -> None:
    tutor = TutorCreate.model_validate(
        {
            "title": "Tutor",
            "system_instructions": "Instrucoes suficientes para passar.",
            "slug": "Suporte Técnico!",
        }
    )

    assert tutor.slug == "suporte-tecnico"
