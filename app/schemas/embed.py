"""Schemas for embed keys and the snippet handed to integrators."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.origins import InvalidOriginError, normalize_all

Label = Annotated[str, Field(default="", max_length=120)]


class EmbedKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label = ""
    allowed_origins: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Origens autorizadas a carregar o widget, no formato scheme://host[:porta]. "
            "Lista vazia libera qualquer origem e so e aceita em ambiente local."
        ),
    )

    @field_validator("allowed_origins")
    @classmethod
    def _normalize(cls, value: list[str]) -> list[str]:
        try:
            return normalize_all(value)
        except InvalidOriginError as exc:
            raise ValueError(str(exc)) from exc


class EmbedKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tutor_id: str
    public_key: str
    label: str
    allowed_origins: list[str]
    is_active: bool
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class EmbedConfig(BaseModel):
    """Public framing policy of a key.

    Contains no information about the tutor or its content: only who may frame the widget page,
    which is exactly what the frontend needs to build its `frame-ancestors` header.
    """

    allowed_origins: list[str]
    allows_any_origin: bool
    is_active: bool


class EmbedSnippet(BaseModel):
    """Everything an integrator needs to embed the tutor (PRD 3.2)."""

    tutor_id: str
    tutor_title: str
    public_key: str
    embed_url: str
    iframe_html: str
    allowed_origins: list[str]
    notes: list[str]
