"""Administrator authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AuthServiceDep, ClientIpDep, CurrentAdmin, SettingsDep, login_rate_limiter
from app.schemas.auth import AdminProfile, LoginRequest, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Autentica um administrador",
    description=(
        "Limitado por IP: este e o unico endpoint que verifica senha, e sem teto seria um alvo "
        "de forca bruta."
    ),
    responses={
        401: {"description": "E-mail ou senha invalidos"},
        429: {"description": "Muitas tentativas de login"},
    },
)
async def login(
    payload: LoginRequest,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
    caller_ip: ClientIpDep,
) -> TokenResponse:
    # Checked before the password is verified: the point is to bound attempts, and bcrypt is
    # deliberately slow enough that unbounded attempts are also a CPU exhaustion vector.
    login_rate_limiter(settings).check(caller_ip)

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
