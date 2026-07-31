"""Request and response schemas for tutor management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.db.models.tutor import SourceKind, TutorStatus

Title = Annotated[str, Field(min_length=3, max_length=120)]
Description = Annotated[str, Field(default="", max_length=280)]
Instructions = Annotated[str, Field(min_length=10, max_length=8_000)]
Label = Annotated[str, Field(min_length=1, max_length=120)]


class ModelSettings(BaseModel):
    """Per-tutor overrides for the LLM call. Omitted fields fall back to the global defaults."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=64)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tool_calls: int | None = Field(default=None, ge=1, le=20)
    max_output_tokens: int | None = Field(default=None, ge=64, le=8_192)


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    label: Label
    url: HttpUrl | None = None
    content: str | None = Field(default=None, max_length=200_000)
    max_bytes: int = Field(default=512_000, ge=1_000, le=2_000_000)

    @model_validator(mode="after")
    def _check_payload_matches_kind(self) -> SourceCreate:
        if self.kind is SourceKind.URL:
            if self.url is None:
                msg = "kind='url' exige o campo 'url'"
                raise ValueError(msg)
            if self.content is not None:
                msg = "kind='url' nao aceita o campo 'content'"
                raise ValueError(msg)
        else:
            if not (self.content or "").strip():
                msg = "kind='inline_text' exige o campo 'content'"
                raise ValueError(msg)
            if self.url is not None:
                msg = "kind='inline_text' nao aceita o campo 'url'"
                raise ValueError(msg)
        return self


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: SourceKind
    label: str
    url: str | None
    max_bytes: int
    is_active: bool
    created_at: datetime


class TutorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description = ""
    system_instructions: Instructions
    greeting: str | None = Field(default=None, max_length=500)
    slug: str | None = Field(default=None, min_length=3, max_length=140)
    status: TutorStatus = TutorStatus.ACTIVE
    model_settings: ModelSettings = Field(default_factory=ModelSettings)
    sources: list[SourceCreate] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.services.slug import normalize_slug

        return normalize_slug(value)


class TutorUpdate(BaseModel):
    """Partial update. Only the provided fields are changed."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: str | None = Field(default=None, max_length=280)
    system_instructions: Instructions | None = None
    greeting: str | None = Field(default=None, max_length=500)
    status: TutorStatus | None = None
    model_settings: ModelSettings | None = None


class TutorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    slug: str
    description: str
    system_instructions: str
    greeting: str | None
    status: TutorStatus
    model_settings: dict[str, Any]
    sources: list[SourceRead]
    created_at: datetime
    updated_at: datetime


class TutorSummary(BaseModel):
    """Lightweight projection used by the list endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    slug: str
    description: str
    status: TutorStatus
    created_at: datetime
    updated_at: datetime


class TutorPage(BaseModel):
    items: list[TutorSummary]
    total: int
    page: int
    size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))


class TutorListQuery(BaseModel):
    """Validated query string of the list endpoint."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=120)
    status: TutorStatus | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
    order: Literal["created_at", "-created_at", "title", "-title"] = "-created_at"
