"""Tutor management API (PRD 4.1.2).

The administrator guard is declared once at the router level, so no route can be added later
without authentication by forgetting a parameter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import SessionDep, TutorServiceDep, require_admin
from app.db.models.tutor import TutorStatus
from app.schemas.tutor import (
    SourceCreate,
    SourceRead,
    TutorCreate,
    TutorListQuery,
    TutorPage,
    TutorRead,
    TutorSummary,
    TutorUpdate,
)

router = APIRouter(
    prefix="/api/v1/tutors",
    tags=["tutors"],
    dependencies=[Depends(require_admin)],
    responses={401: {"description": "Token de administrador ausente ou invalido"}},
)


@router.post(
    "",
    response_model=TutorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria um tutor",
    responses={409: {"description": "Slug ja utilizado ou limite de fontes atingido"}},
)
async def create_tutor(
    payload: TutorCreate, service: TutorServiceDep, session: SessionDep
) -> TutorRead:
    tutor = await service.create(payload)
    await session.commit()
    return TutorRead.model_validate(tutor)


@router.get("", response_model=TutorPage, summary="Lista tutores")
async def list_tutors(
    service: TutorServiceDep, query: Annotated[TutorListQuery, Query()]
) -> TutorPage:
    tutors, total = await service.list_page(query)
    return TutorPage(
        items=[TutorSummary.model_validate(tutor) for tutor in tutors],
        total=total,
        page=query.page,
        size=query.size,
    )


@router.get(
    "/{tutor_id}",
    response_model=TutorRead,
    summary="Detalha um tutor",
    responses={404: {"description": "Tutor nao encontrado"}},
)
async def get_tutor(tutor_id: str, service: TutorServiceDep) -> TutorRead:
    tutor = await service.get(tutor_id)
    return TutorRead.model_validate(tutor)


@router.patch(
    "/{tutor_id}",
    response_model=TutorRead,
    summary="Edita um tutor",
    description="Atualizacao parcial: apenas os campos enviados sao alterados.",
    responses={404: {"description": "Tutor nao encontrado"}},
)
async def update_tutor(
    tutor_id: str, payload: TutorUpdate, service: TutorServiceDep, session: SessionDep
) -> TutorRead:
    tutor = await service.update(tutor_id, payload)
    await session.commit()
    return TutorRead.model_validate(tutor)


@router.post("/{tutor_id}/activate", response_model=TutorRead, summary="Ativa um tutor")
async def activate_tutor(tutor_id: str, service: TutorServiceDep, session: SessionDep) -> TutorRead:
    tutor = await service.set_status(tutor_id, TutorStatus.ACTIVE)
    await session.commit()
    return TutorRead.model_validate(tutor)


@router.post(
    "/{tutor_id}/deactivate",
    response_model=TutorRead,
    summary="Desativa um tutor",
    description=(
        "Desativar preserva o tutor, o historico e os embeds ja publicados: o widget passa a "
        "informar indisponibilidade em vez de quebrar."
    ),
)
async def deactivate_tutor(
    tutor_id: str, service: TutorServiceDep, session: SessionDep
) -> TutorRead:
    tutor = await service.set_status(tutor_id, TutorStatus.INACTIVE)
    await session.commit()
    return TutorRead.model_validate(tutor)


@router.delete(
    "/{tutor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove um tutor definitivamente",
    description="Prefira desativar. A remocao apaga fontes, chaves, sessoes e historico.",
)
async def delete_tutor(tutor_id: str, service: TutorServiceDep, session: SessionDep) -> Response:
    await service.delete(tutor_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- sources ---------------------------------------------------------------


@router.post(
    "/{tutor_id}/sources",
    response_model=SourceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Adiciona uma fonte de conhecimento",
    responses={409: {"description": "Limite de fontes por tutor atingido"}},
)
async def add_source(
    tutor_id: str, payload: SourceCreate, service: TutorServiceDep, session: SessionDep
) -> SourceRead:
    source = await service.add_source(tutor_id, payload)
    await session.commit()
    return SourceRead.model_validate(source)


@router.delete(
    "/{tutor_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove uma fonte de conhecimento",
    responses={404: {"description": "Fonte nao encontrada neste tutor"}},
)
async def remove_source(
    tutor_id: str, source_id: str, service: TutorServiceDep, session: SessionDep
) -> Response:
    await service.remove_source(tutor_id, source_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
