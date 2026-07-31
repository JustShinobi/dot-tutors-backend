"""Data access for embed keys."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.embed import EmbedKey


class EmbedKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key_id: str) -> EmbedKey | None:
        return await self._session.get(EmbedKey, key_id)

    async def get_by_public_key(self, public_key: str) -> EmbedKey | None:
        """Resolve a key together with its tutor.

        The tutor is eager-loaded because the caller always needs it right away, to check that
        it is still active before opening a session.
        """
        result = await self._session.execute(
            select(EmbedKey)
            .where(EmbedKey.public_key == public_key)
            .options(selectinload(EmbedKey.tutor))
        )
        return result.scalar_one_or_none()

    async def list_for_tutor(self, tutor_id: str) -> list[EmbedKey]:
        result = await self._session.execute(
            select(EmbedKey)
            .where(EmbedKey.tutor_id == tutor_id)
            .order_by(EmbedKey.created_at.desc())
        )
        return list(result.scalars().unique())

    async def public_key_exists(self, public_key: str) -> bool:
        result = await self._session.execute(
            select(EmbedKey.id).where(EmbedKey.public_key == public_key).limit(1)
        )
        return result.scalar_one_or_none() is not None

    def add(self, key: EmbedKey) -> EmbedKey:
        self._session.add(key)
        return key

    async def flush(self) -> None:
        await self._session.flush()
