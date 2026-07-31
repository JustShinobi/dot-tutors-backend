"""Tutor management rules (PRD 3.1 and 4.1).

Keeps every business rule out of the HTTP layer: uniqueness of the slug, the source limit, and
the fact that a tutor is *deactivated*, never silently deleted, so existing embeds degrade into
a clear message instead of a 404.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import (
    SlugAlreadyUsedError,
    SourceLimitReachedError,
    SourceNotFoundError,
    TutorNotFoundError,
)
from app.core.logging import get_logger
from app.db.models.tutor import SourceKind, Tutor, TutorSource, TutorStatus
from app.repositories.tutor import SourceRepository, TutorRepository
from app.schemas.tutor import SourceCreate, TutorCreate, TutorListQuery, TutorUpdate
from app.services.slug import generate_unique_slug

logger = get_logger(__name__)


class TutorService:
    def __init__(
        self,
        *,
        tutors: TutorRepository,
        sources: SourceRepository,
        settings: Settings,
    ) -> None:
        self._tutors = tutors
        self._sources = sources
        self._settings = settings

    # --- reads -------------------------------------------------------------

    async def get(self, tutor_id: str) -> Tutor:
        tutor = await self._tutors.get(tutor_id)
        if tutor is None:
            raise TutorNotFoundError
        return tutor

    async def list_page(self, query: TutorListQuery) -> tuple[list[Tutor], int]:
        return await self._tutors.list_page(query)

    # --- writes ------------------------------------------------------------

    async def create(self, payload: TutorCreate) -> Tutor:
        if len(payload.sources) > self._settings.max_sources_per_tutor:
            raise SourceLimitReachedError(
                f"Um tutor aceita no maximo {self._settings.max_sources_per_tutor} fontes."
            )

        slug = await self._resolve_slug(payload.slug, fallback=payload.title)

        tutor = Tutor(
            title=payload.title.strip(),
            slug=slug,
            description=payload.description.strip(),
            system_instructions=payload.system_instructions.strip(),
            greeting=(payload.greeting or "").strip() or None,
            status=payload.status,
            model_settings=payload.model_settings.model_dump(exclude_none=True),
        )
        for source_payload in payload.sources:
            tutor.sources.append(_build_source(source_payload))

        self._tutors.add(tutor)
        await self._tutors.flush()
        # Reload eagerly: after the flush the relationships of a freshly persisted object are
        # unloaded, and touching them later (to serialise, or just to log) would trigger a lazy
        # load outside the async context.
        await self._tutors.refresh(tutor)

        logger.info(
            "tutor_created",
            tutor_id=tutor.id,
            slug=tutor.slug,
            status=str(tutor.status),
            source_count=len(payload.sources),
        )
        return tutor

    async def update(self, tutor_id: str, payload: TutorUpdate) -> Tutor:
        tutor = await self.get(tutor_id)
        changes = payload.model_dump(exclude_unset=True)

        if "title" in changes and changes["title"] is not None:
            tutor.title = changes["title"].strip()
        if "description" in changes and changes["description"] is not None:
            tutor.description = changes["description"].strip()
        if "system_instructions" in changes and changes["system_instructions"] is not None:
            tutor.system_instructions = changes["system_instructions"].strip()
        if "greeting" in changes:
            greeting = (changes["greeting"] or "").strip()
            tutor.greeting = greeting or None
        if "status" in changes and changes["status"] is not None:
            tutor.status = changes["status"]
        if "model_settings" in changes and payload.model_settings is not None:
            tutor.model_settings = payload.model_settings.model_dump(exclude_none=True)

        await self._tutors.flush()
        logger.info("tutor_updated", tutor_id=tutor.id, fields=sorted(changes))
        return tutor

    async def set_status(self, tutor_id: str, status: TutorStatus) -> Tutor:
        """Activate or deactivate. Deactivating keeps history and embeds intact (PRD 3.1)."""
        tutor = await self.get(tutor_id)
        if tutor.status is not status:
            tutor.status = status
            await self._tutors.flush()
            logger.info("tutor_status_changed", tutor_id=tutor.id, status=str(status))
        return tutor

    async def delete(self, tutor_id: str) -> None:
        tutor = await self.get(tutor_id)
        await self._tutors.delete(tutor)
        await self._tutors.flush()
        logger.info("tutor_deleted", tutor_id=tutor_id)

    # --- sources -----------------------------------------------------------

    async def add_source(self, tutor_id: str, payload: SourceCreate) -> TutorSource:
        tutor = await self.get(tutor_id)

        current = await self._sources.count_for_tutor(tutor.id)
        if current >= self._settings.max_sources_per_tutor:
            raise SourceLimitReachedError(
                f"Um tutor aceita no maximo {self._settings.max_sources_per_tutor} fontes."
            )

        source = _build_source(payload)
        source.tutor_id = tutor.id
        self._sources.add(source)
        await self._tutors.flush()

        logger.info(
            "source_added",
            tutor_id=tutor.id,
            source_id=source.id,
            kind=str(source.kind),
            has_url=source.url is not None,
        )
        return source

    async def remove_source(self, tutor_id: str, source_id: str) -> None:
        source = await self._sources.get_for_tutor(tutor_id, source_id)
        if source is None:
            raise SourceNotFoundError
        await self._sources.delete(source)
        await self._tutors.flush()
        logger.info("source_removed", tutor_id=tutor_id, source_id=source_id)

    # --- helpers -----------------------------------------------------------

    async def _resolve_slug(self, requested: str | None, *, fallback: str) -> str:
        if requested:
            if await self._tutors.slug_exists(requested):
                raise SlugAlreadyUsedError
            return requested
        return await generate_unique_slug(fallback, exists=self._tutors.slug_exists)


def _build_source(payload: SourceCreate) -> TutorSource:
    return TutorSource(
        kind=payload.kind,
        label=payload.label.strip(),
        url=str(payload.url) if payload.kind is SourceKind.URL and payload.url else None,
        content=payload.content if payload.kind is SourceKind.INLINE_TEXT else None,
        max_bytes=payload.max_bytes,
        is_active=True,
    )
