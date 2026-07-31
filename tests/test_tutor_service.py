"""Business rules of tutor management (PRD 3.1 and 4.1)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    SlugAlreadyUsedError,
    SourceLimitReachedError,
    SourceNotFoundError,
    TutorNotFoundError,
)
from app.db.models.tutor import SourceKind, TutorStatus
from app.schemas.tutor import (
    ModelSettings,
    SourceCreate,
    TutorCreate,
    TutorListQuery,
    TutorUpdate,
)
from app.services.tutor_service import TutorService


def _payload(**overrides: object) -> TutorCreate:
    data: dict[str, object] = {
        "title": "Tutor de Onboarding",
        "description": "Ajuda novos colaboradores",
        "system_instructions": "Voce e um tutor de onboarding. Responda com base nas fontes.",
    }
    data.update(overrides)
    return TutorCreate.model_validate(data)


async def test_create_derives_slug_from_title(tutor_service: TutorService) -> None:
    tutor = await tutor_service.create(_payload())

    assert tutor.slug == "tutor-de-onboarding"
    assert tutor.status is TutorStatus.ACTIVE
    assert tutor.is_active


async def test_create_disambiguates_slug_for_duplicate_titles(
    tutor_service: TutorService,
) -> None:
    first = await tutor_service.create(_payload())
    second = await tutor_service.create(_payload())

    assert first.slug == "tutor-de-onboarding"
    assert second.slug == "tutor-de-onboarding-2"


async def test_create_rejects_explicit_slug_already_in_use(tutor_service: TutorService) -> None:
    await tutor_service.create(_payload(slug="suporte"))

    with pytest.raises(SlugAlreadyUsedError):
        await tutor_service.create(_payload(title="Outro", slug="suporte"))


async def test_create_trims_whitespace_and_normalizes_empty_greeting(
    tutor_service: TutorService,
) -> None:
    tutor = await tutor_service.create(
        _payload(title="  Tutor Espacado  ", greeting="   ", description="  texto  ")
    )

    assert tutor.title == "Tutor Espacado"
    assert tutor.description == "texto"
    assert tutor.greeting is None


async def test_create_persists_model_settings_without_null_noise(
    tutor_service: TutorService,
) -> None:
    tutor = await tutor_service.create(
        _payload(model_settings=ModelSettings(temperature=0.2, max_tool_calls=4))
    )

    assert tutor.model_settings == {"temperature": 0.2, "max_tool_calls": 4}


async def test_create_accepts_inline_and_url_sources(tutor_service: TutorService) -> None:
    tutor = await tutor_service.create(
        _payload(
            sources=[
                SourceCreate(
                    kind=SourceKind.URL,
                    label="Guia publico",
                    url="https://example.com/guia.md",  # type: ignore[arg-type]
                ),
                SourceCreate(
                    kind=SourceKind.INLINE_TEXT,
                    label="FAQ interno",
                    content="Pergunta e resposta.",
                ),
            ]
        )
    )

    assert [source.label for source in tutor.sources] == ["Guia publico", "FAQ interno"]
    assert tutor.sources[0].url == "https://example.com/guia.md"
    assert tutor.sources[0].content is None
    assert tutor.sources[1].url is None


async def test_create_enforces_the_source_limit(tutor_service: TutorService) -> None:
    too_many = [
        SourceCreate(kind=SourceKind.INLINE_TEXT, label=f"Fonte {index}", content="texto")
        for index in range(11)
    ]

    with pytest.raises(SourceLimitReachedError):
        await tutor_service.create(_payload(sources=too_many))


async def test_update_changes_only_the_provided_fields(tutor_service: TutorService) -> None:
    tutor = await tutor_service.create(_payload())
    original_instructions = tutor.system_instructions

    updated = await tutor_service.update(tutor.id, TutorUpdate(title="Tutor Renomeado"))

    assert updated.title == "Tutor Renomeado"
    assert updated.system_instructions == original_instructions
    # The slug is stable on purpose: it is already published in admin URLs.
    assert updated.slug == "tutor-de-onboarding"


async def test_deactivate_keeps_the_tutor_and_its_sources(
    tutor_service: TutorService, session: AsyncSession
) -> None:
    tutor = await tutor_service.create(
        _payload(
            sources=[
                SourceCreate(kind=SourceKind.INLINE_TEXT, label="FAQ", content="conteudo"),
            ]
        )
    )

    deactivated = await tutor_service.set_status(tutor.id, TutorStatus.INACTIVE)
    await session.commit()

    assert deactivated.status is TutorStatus.INACTIVE
    assert deactivated.is_active is False
    assert len(deactivated.sources) == 1


async def test_set_status_is_idempotent(tutor_service: TutorService) -> None:
    tutor = await tutor_service.create(_payload())

    await tutor_service.set_status(tutor.id, TutorStatus.ACTIVE)
    again = await tutor_service.set_status(tutor.id, TutorStatus.ACTIVE)

    assert again.status is TutorStatus.ACTIVE


async def test_get_unknown_tutor_raises_domain_error(tutor_service: TutorService) -> None:
    with pytest.raises(TutorNotFoundError):
        await tutor_service.get("00000000-0000-0000-0000-000000000000")


async def test_add_and_remove_source(tutor_service: TutorService) -> None:
    tutor = await tutor_service.create(_payload())

    source = await tutor_service.add_source(
        tutor.id,
        SourceCreate(kind=SourceKind.INLINE_TEXT, label="Politica", content="texto"),
    )
    await tutor_service.remove_source(tutor.id, source.id)

    with pytest.raises(SourceNotFoundError):
        await tutor_service.remove_source(tutor.id, source.id)


async def test_remove_source_of_another_tutor_is_not_found(tutor_service: TutorService) -> None:
    owner = await tutor_service.create(_payload(title="Dono"))
    other = await tutor_service.create(_payload(title="Outro"))
    source = await tutor_service.add_source(
        owner.id, SourceCreate(kind=SourceKind.INLINE_TEXT, label="F", content="t")
    )

    with pytest.raises(SourceNotFoundError):
        await tutor_service.remove_source(other.id, source.id)


async def test_deleting_a_tutor_cascades_to_its_sources(
    tutor_service: TutorService, session: AsyncSession
) -> None:
    from sqlalchemy import func, select

    from app.db.models.tutor import TutorSource

    tutor = await tutor_service.create(
        _payload(sources=[SourceCreate(kind=SourceKind.INLINE_TEXT, label="F", content="t")])
    )
    await tutor_service.delete(tutor.id)

    remaining = await session.execute(select(func.count()).select_from(TutorSource))
    assert remaining.scalar_one() == 0


async def test_list_filters_by_status_and_search_term(tutor_service: TutorService) -> None:
    await tutor_service.create(_payload(title="Tutor de Vendas"))
    await tutor_service.create(_payload(title="Tutor de Suporte", status=TutorStatus.INACTIVE))

    active, active_total = await tutor_service.list_page(TutorListQuery(status=TutorStatus.ACTIVE))
    searched, searched_total = await tutor_service.list_page(TutorListQuery(q="suporte"))

    assert active_total == 1
    assert active[0].title == "Tutor de Vendas"
    assert searched_total == 1
    assert searched[0].title == "Tutor de Suporte"


async def test_list_paginates(tutor_service: TutorService) -> None:
    for index in range(5):
        await tutor_service.create(_payload(title=f"Tutor {index}"))

    first_page, total = await tutor_service.list_page(TutorListQuery(page=1, size=2))
    second_page, _ = await tutor_service.list_page(TutorListQuery(page=2, size=2))

    assert total == 5
    assert len(first_page) == 2
    assert {tutor.id for tutor in first_page}.isdisjoint({tutor.id for tutor in second_page})
