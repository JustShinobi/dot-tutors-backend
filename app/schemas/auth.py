"""Authentication schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    # "bearer" here is the OAuth token *scheme*, not a credential.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Validade do token, em segundos.")


class AdminProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
