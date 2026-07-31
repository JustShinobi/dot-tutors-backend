"""Embed key management (PRD 3.2). Administrator-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import EmbedServiceDep, SessionDep, require_admin
from app.schemas.embed import EmbedKeyCreate, EmbedKeyRead, EmbedSnippet

router = APIRouter(
    prefix="/api/v1",
    tags=["embed-keys"],
    dependencies=[Depends(require_admin)],
    responses={401: {"description": "Token de administrador ausente ou invalido"}},
)


@router.post(
    "/tutors/{tutor_id}/embed-keys",
    response_model=EmbedKeyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma chave de embed",
    description=(
        "A chave retornada e publica: ela vai no atributo src do iframe e fica visivel no HTML "
        "do integrador. A protecao do tutor vem da lista de origens permitidas, nao do sigilo "
        "da chave."
    ),
)
async def create_embed_key(
    tutor_id: str, payload: EmbedKeyCreate, service: EmbedServiceDep, session: SessionDep
) -> EmbedKeyRead:
    key = await service.create_key(tutor_id, payload)
    await session.commit()
    return EmbedKeyRead.model_validate(key)


@router.get(
    "/tutors/{tutor_id}/embed-keys",
    response_model=list[EmbedKeyRead],
    summary="Lista as chaves de embed de um tutor",
)
async def list_embed_keys(tutor_id: str, service: EmbedServiceDep) -> list[EmbedKeyRead]:
    keys = await service.list_keys(tutor_id)
    return [EmbedKeyRead.model_validate(key) for key in keys]


@router.post(
    "/embed-keys/{key_id}/revoke",
    response_model=EmbedKeyRead,
    summary="Revoga uma chave de embed",
    description=(
        "Efeito imediato: alem de recusar novas sessoes, as sessoes ja abertas com esta chave "
        "param de responder na proxima mensagem."
    ),
    responses={404: {"description": "Chave nao encontrada"}},
)
async def revoke_embed_key(
    key_id: str, service: EmbedServiceDep, session: SessionDep
) -> EmbedKeyRead:
    key = await service.revoke_key(key_id)
    await session.commit()
    return EmbedKeyRead.model_validate(key)


@router.get(
    "/tutors/{tutor_id}/embed-snippet",
    response_model=EmbedSnippet,
    summary="Snippet de incorporacao pronto para o integrador",
    responses={404: {"description": "Tutor ou chave nao encontrados"}},
)
async def get_embed_snippet(tutor_id: str, key_id: str, service: EmbedServiceDep) -> EmbedSnippet:
    return await service.build_snippet(tutor_id, key_id)
