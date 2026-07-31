"""Data access for tutors and their sources."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.tutor import Tutor, TutorSource, TutorStatus
from app.schemas.tutor import TutorListQuery

_ORDER_COLUMNS: dict[str, ColumnElement[Any]] = {
    "created_at": Tutor.created_at.asc(),
    "-created_at": Tutor.created_at.desc(),
    "title": Tutor.title.asc(),
    "-title": Tutor.title.desc(),
}


class TutorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- reads -------------------------------------------------------------

    async def get(self, tutor_id: str) -> Tutor | None:
        return await self._session.get(Tutor, tutor_id)

    async def get_by_slug(self, slug: str) -> Tutor | None:
        result = await self._session.execute(select(Tutor).where(Tutor.slug == slug))
        return result.scalar_one_or_none()

    async def slug_exists(self, slug: str) -> bool:
        result = await self._session.execute(
            select(func.count()).select_from(Tutor).where(Tutor.slug == slug)
        )
        return bool(result.scalar_one())

    async def list_page(self, query: TutorListQuery) -> tuple[list[Tutor], int]:
        """Return one page of tutors plus the total number of matches."""
        statement = self._apply_filters(select(Tutor), query)

        total_result = await self._session.execute(
            self._apply_filters(select(func.count()).select_from(Tutor), query)
        )
        total = int(total_result.scalar_one())

        page_result = await self._session.execute(
            statement.order_by(_ORDER_COLUMNS[query.order])
            .offset((query.page - 1) * query.size)
            .limit(query.size)
        )
        return list(page_result.scalars().unique()), total

    @staticmethod
    def _apply_filters[T: tuple[Any, ...]](
        statement: Select[T], query: TutorListQuery
    ) -> Select[T]:
        if query.status is not None:
            statement = statement.where(Tutor.status == query.status)
        if query.q:
            pattern = f"%{query.q.strip().lower()}%"
            statement = statement.where(
                or_(
                    func.lower(Tutor.title).like(pattern),
                    func.lower(Tutor.description).like(pattern),
                    func.lower(Tutor.slug).like(pattern),
                )
            )
        return statement

    # --- writes ------------------------------------------------------------

    def add(self, tutor: Tutor) -> Tutor:
        self._session.add(tutor)
        return tutor

    async def delete(self, tutor: Tutor) -> None:
        await self._session.delete(tutor)

    async def flush(self) -> None:
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def refresh(self, tutor: Tutor) -> None:
        await self._session.refresh(tutor)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, source_id: str) -> TutorSource | None:
        return await self._session.get(TutorSource, source_id)

    async def get_for_tutor(self, tutor_id: str, source_id: str) -> TutorSource | None:
        result = await self._session.execute(
            select(TutorSource).where(TutorSource.id == source_id, TutorSource.tutor_id == tutor_id)
        )
        return result.scalar_one_or_none()

    async def list_active_for_tutor(self, tutor_id: str) -> list[TutorSource]:
        result = await self._session.execute(
            select(TutorSource)
            .where(TutorSource.tutor_id == tutor_id, TutorSource.is_active.is_(True))
            .order_by(TutorSource.created_at)
        )
        return list(result.scalars().unique())

    async def count_for_tutor(self, tutor_id: str) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(TutorSource).where(TutorSource.tutor_id == tutor_id)
        )
        return int(result.scalar_one())

    def add(self, source: TutorSource) -> TutorSource:
        self._session.add(source)
        return source

    async def delete(self, source: TutorSource) -> None:
        await self._session.delete(source)


def active_tutor_statement() -> Select[tuple[Tutor]]:
    """Reusable filter for the embed runtime, which must never serve an inactive tutor."""
    return select(Tutor).where(Tutor.status == TutorStatus.ACTIVE)
