"""Knowledge sources: fetching, caching and lexical retrieval (PRD 4.3.2).

This is the layer the agent's tools sit on. It is deliberately free of any LLM concept — no
prompt, no model, no framework — so the same logic serves both the Pydantic AI runner and the
alternative LangGraph one, and can be tested without a model at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import SourceFetchError, SourceNotFoundError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models.source_cache import SourceDocument
from app.db.models.tutor import SourceKind, TutorSource
from app.utils.http_fetch import FetchedDocument, fetch_text
from app.utils.text import Chunk, ScoredChunk, build_outline, chunk_document, search_chunks

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """What the agent is told about a source before deciding to open it."""

    source_id: str
    label: str
    kind: str
    url: str | None
    characters: int
    section_count: int
    available: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class LoadedSource:
    source: TutorSource
    text: str
    outline: list[dict[str, object]]
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.error is None and bool(self.text)


@dataclass(frozen=True, slots=True)
class _FetchPlan:
    """What phase 2 needs to know, carried across the phase boundary."""

    source: TutorSource
    document: SourceDocument | None


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    """The result of the network phase — one of the two is always `None`."""

    document: FetchedDocument | None
    error: str | None


class SourceService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._session = session
        self._settings = settings
        self._http = http_client
        self._chunk_cache: dict[str, list[Chunk]] = {}

    # --- catalogue ---------------------------------------------------------

    async def list_sources(self, tutor_id: str) -> list[SourceInfo]:
        """Catalogue of the tutor's sources, loading each one (from cache when fresh).

        This runs on every turn — the catalogue is injected into the instructions — so with a
        cold cache the cost is one HTTP round trip per source. `load_many` overlaps those.
        """
        sources = await self._active_sources(tutor_id)
        if not sources:
            return []

        loaded_all = await self.load_many(sources)
        return [_info(loaded) for loaded in loaded_all]

    async def describe(self, source: TutorSource) -> SourceInfo:
        """Cache state of a single source, for the administrator.

        Adding a URL the fetcher cannot reach used to fail silently until a user asked a
        question. This is what lets the panel say so at configuration time instead.
        """
        return _info(await self.load(source))

    async def refresh(self, source: TutorSource) -> SourceInfo:
        """Force a refetch, ignoring the TTL (PRD 4.1.1).

        Without this, correcting a broken URL or picking up an edited document means waiting out
        `SOURCE_CACHE_TTL_MINUTES`.
        """
        return _info(await self.load(source, force_refresh=True))

    async def get_source(self, tutor_id: str, source_id: str) -> TutorSource:
        result = await self._session.execute(
            select(TutorSource).where(
                TutorSource.id == source_id,
                TutorSource.tutor_id == tutor_id,
                TutorSource.is_active.is_(True),
            )
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise SourceNotFoundError
        return source

    # --- content -----------------------------------------------------------

    async def load(self, source: TutorSource, *, force_refresh: bool = False) -> LoadedSource:
        """Return the text of a source, fetching and caching it when needed.

        A failure never propagates as an exception: the agent must be able to say "this source
        is unavailable" and keep answering with the others, instead of the whole conversation
        dying because one URL is down.
        """
        plan, cached = await self._plan(source, force_refresh=force_refresh)
        if cached is not None:
            return cached

        outcome = await self._download(plan)
        return await self._store(plan, outcome)

    async def load_many(self, sources: Sequence[TutorSource]) -> list[LoadedSource]:
        """Load several sources, overlapping the network but never the database.

        Splitting the work into three phases is not decoration. `AsyncSession` is explicitly not
        safe for concurrent use — two coroutines issuing statements on one session raise
        `IllegalStateChangeError` rather than queueing — so only the middle phase, which touches
        nothing but HTTP, can run under `gather`. That is also where the time goes: with a cold
        cache, doing this sequentially costs one timeout *per source* before the first token,
        which on a tutor with several sources can exceed the agent timeout by itself.
        """
        pending: list[_FetchPlan] = []
        resolved: dict[str, LoadedSource] = {}

        # Phase 1 — database, sequential.
        for source in sources:
            plan, cached = await self._plan(source, force_refresh=False)
            if cached is not None:
                resolved[source.id] = cached
            else:
                pending.append(plan)

        # Phase 2 — network, concurrent.
        outcomes = await asyncio.gather(*(self._download(plan) for plan in pending))

        # Phase 3 — database, sequential.
        for plan, outcome in zip(pending, outcomes, strict=True):
            resolved[plan.source.id] = await self._store(plan, outcome)

        return [resolved[source.id] for source in sources]

    async def _plan(
        self, source: TutorSource, *, force_refresh: bool
    ) -> tuple[_FetchPlan, LoadedSource | None]:
        """Decide what has to happen. Touches the database, never the network."""
        if source.kind is SourceKind.INLINE_TEXT:
            text = source.content or ""
            answer = LoadedSource(
                source=source,
                text=text,
                outline=[_section_dict(section) for section in build_outline(text)],
            )
            return _FetchPlan(source=source, document=None), answer

        document = await self._cached_document(source.id)
        if document is not None and document.is_fresh() and not force_refresh:
            if document.fetch_error:
                answer = LoadedSource(
                    source=source, text="", outline=[], error=document.fetch_error
                )
            else:
                answer = LoadedSource(source=source, text=document.text, outline=document.outline)
            return _FetchPlan(source=source, document=document), answer

        return _FetchPlan(source=source, document=document), None

    async def _download(self, plan: _FetchPlan) -> _FetchOutcome:
        """Fetch one source. Touches the network, never the database."""
        source = plan.source
        assert source.url is not None

        try:
            fetched = await fetch_text(
                source.url,
                client=self._http,
                max_bytes=min(source.max_bytes, self._settings.source_max_bytes),
                timeout_seconds=self._settings.source_fetch_timeout_seconds,
                etag=plan.document.etag if plan.document else None,
                last_modified=plan.document.last_modified if plan.document else None,
                allow_private_network=self._settings.source_allow_private_network,
            )
        except SourceFetchError as exc:
            logger.warning(
                "source_fetch_failed",
                source_id=source.id,
                url_host=httpx.URL(source.url).host,
                error=exc.message,
            )
            return _FetchOutcome(document=None, error=exc.message)

        return _FetchOutcome(document=fetched, error=None)

    async def _store(self, plan: _FetchPlan, outcome: _FetchOutcome) -> LoadedSource:
        """Persist the result. Touches the database, never the network."""
        source = plan.source
        document = plan.document

        if outcome.error is not None:
            # A stale copy is better than nothing: the agent keeps answering from what it has
            # and only reports unavailability when there is genuinely no text.
            return LoadedSource(
                source=source,
                text=document.text if document else "",
                outline=document.outline if document else [],
                error=None if document and document.text else outcome.error,
            )

        fetched = outcome.document
        assert fetched is not None
        expires_at = utcnow() + timedelta(minutes=self._settings.source_cache_ttl_minutes)

        if fetched.not_modified and document is not None:
            document.expires_at = expires_at
            document.fetched_at = utcnow()
            await self._session.flush()
            logger.info("source_not_modified", source_id=source.id)
            return LoadedSource(source=source, text=document.text, outline=document.outline)

        outline = [_section_dict(section) for section in build_outline(fetched.text)]

        if document is None:
            document = SourceDocument(source_id=source.id, expires_at=expires_at)
            self._session.add(document)

        document.text = fetched.text
        document.outline = outline
        document.byte_size = fetched.byte_size
        document.etag = fetched.etag
        document.last_modified = fetched.last_modified
        document.fetched_at = utcnow()
        document.expires_at = expires_at
        document.fetch_error = None
        await self._session.flush()

        self._chunk_cache.pop(source.id, None)

        logger.info(
            "source_fetched",
            source_id=source.id,
            url_host=httpx.URL(source.url).host if source.url else None,
            bytes=fetched.byte_size,
            sections=len(outline),
        )
        return LoadedSource(source=source, text=fetched.text, outline=outline)

    # --- retrieval ---------------------------------------------------------

    async def search(self, source: TutorSource, query: str, *, limit: int = 3) -> list[ScoredChunk]:
        """Lexical BM25 search inside one source. No embeddings, no vector index (PRD 6.2)."""
        loaded = await self.load(source)
        if not loaded.available:
            return []
        return search_chunks(await self._chunks(source, loaded.text), query, limit=limit)

    async def read(
        self, source: TutorSource, *, offset: int = 0, max_chars: int = 4_000
    ) -> tuple[str, int, bool]:
        """Sequential paginated read. Returns `(text, next_offset, has_more)`."""
        loaded = await self.load(source)
        if not loaded.available:
            return "", offset, False

        start = max(0, min(offset, len(loaded.text)))
        end = min(start + max_chars, len(loaded.text))
        return loaded.text[start:end], end, end < len(loaded.text)

    async def _chunks(self, source: TutorSource, text: str) -> list[Chunk]:
        cached = self._chunk_cache.get(source.id)
        if cached is None:
            cached = chunk_document(text)
            self._chunk_cache[source.id] = cached
        return cached

    # --- helpers -----------------------------------------------------------

    async def _active_sources(self, tutor_id: str) -> list[TutorSource]:
        result = await self._session.execute(
            select(TutorSource)
            .where(TutorSource.tutor_id == tutor_id, TutorSource.is_active.is_(True))
            .order_by(TutorSource.created_at)
        )
        return list(result.scalars().unique())

    async def _cached_document(self, source_id: str) -> SourceDocument | None:
        result = await self._session.execute(
            select(SourceDocument).where(SourceDocument.source_id == source_id)
        )
        return result.scalar_one_or_none()


def _info(loaded: LoadedSource) -> SourceInfo:
    """Project a loaded source into what the agent and the panel are told about it."""
    source = loaded.source
    return SourceInfo(
        source_id=source.id,
        label=source.label,
        kind=str(source.kind),
        url=source.url,
        characters=len(loaded.text),
        section_count=len(loaded.outline),
        available=loaded.available,
        error=loaded.error,
    )


def _section_dict(section: object) -> dict[str, object]:
    from app.utils.text import Section

    assert isinstance(section, Section)
    return {
        "heading": section.heading,
        "level": section.level,
        "start": section.start,
        "preview": section.preview,
    }
