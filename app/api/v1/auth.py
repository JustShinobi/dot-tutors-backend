"""Administrator authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AuthServiceDep, CurrentAdmin
from app.schemas.auth import AdminProfile, LoginRequest, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Autentica um administrador",
    responses={401: {"description": "E-mail ou senha invalidos"}},
)
async def login(payload: LoginRequest, auth_service: AuthServiceDep) -> TokenResponse:
    access_token, expires_in = await auth_service.authenticate(payload.email, payload.password)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.get(
    "/me",
    response_model=AdminProfile,
    summary="Dados do administrador autenticado",
    status_code=status.HTTP_200_OK,
)
async def me(admin: CurrentAdmin) -> AdminProfile:
    return AdminProfile(id=admin.id, email=admin.email, role=str(admin.role))
