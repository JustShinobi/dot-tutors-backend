"""Tutor management API (PRD 4.1.2).

The administrator guard is declared once at the router level, so no route can be added later
without authentication by forgetting a parameter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    ChatRepositoryDep,
    SessionDep,
    SourceServiceDep,
    TutorServiceDep,
    require_admin,
)
from app.db.models.tutor import TutorStatus
from app.schemas.chat import MessageRead, SessionSummary
from app.schemas.tutor import (
    SourceCreate,
    SourceRead,
    SourceStatus,
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


@router.get(
    "/{tutor_id}/sources/status",
    response_model=list[SourceStatus],
    summary="Estado das fontes como o agente as enxerga",
    description=(
        "Mostra se cada fonte foi obtida com sucesso, quanto texto ela tem e qual erro ocorreu. "
        "Serve para descobrir uma URL quebrada na configuracao, e nao durante uma conversa."
    ),
)
async def get_sources_status(
    tutor_id: str, service: TutorServiceDep, sources: SourceServiceDep, session: SessionDep
) -> list[SourceStatus]:
    await service.get(tutor_id)
    statuses = await sources.list_sources(tutor_id)
    # Loading may have populated or refreshed the cache; keep that work.
    await session.commit()
    return [SourceStatus.model_validate(info, from_attributes=True) for info in statuses]


@router.post(
    "/{tutor_id}/sources/{source_id}/refresh",
    response_model=SourceStatus,
    summary="Forca a releitura de uma fonte",
    description=(
        "Ignora o TTL do cache e busca a fonte novamente. Sem isso, corrigir uma URL quebrada "
        "ou publicar uma versao nova do documento so tem efeito depois de "
        "SOURCE_CACHE_TTL_MINUTES."
    ),
    responses={404: {"description": "Fonte nao encontrada neste tutor"}},
)
async def refresh_source(
    tutor_id: str,
    source_id: str,
    service: TutorServiceDep,
    sources: SourceServiceDep,
    session: SessionDep,
) -> SourceStatus:
    await service.get(tutor_id)
    source = await sources.get_source(tutor_id, source_id)
    info = await sources.refresh(source)
    await session.commit()
    return SourceStatus.model_validate(info, from_attributes=True)


# --- conversations ---------------------------------------------------------


@router.get(
    "/{tutor_id}/sessions",
    response_model=list[SessionSummary],
    summary="Conversas recentes deste tutor",
    description=(
        "Inspecao das ultimas sessoes, com as mensagens de cada uma. Existe para demonstrar e "
        "depurar o comportamento do agente sem ler o banco na mao."
    ),
)
async def list_tutor_sessions(
    tutor_id: str,
    service: TutorServiceDep,
    chats: ChatRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SessionSummary]:
    await service.get(tutor_id)
    sessions = await chats.recent_sessions_for_tutor(tutor_id, limit=limit)

    return [
        SessionSummary(
            id=chat_session.id,
            origin=chat_session.origin,
            created_at=chat_session.created_at,
            last_seen_at=chat_session.last_seen_at,
            message_count=len(chat_session.messages),
            messages=[MessageRead.model_validate(message) for message in chat_session.messages],
        )
        for chat_session in sessions
    ]
