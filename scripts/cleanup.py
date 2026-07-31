"""Delete expired chat sessions and their messages.

Run with `python -m scripts.cleanup`. Meant for a cron entry (or a scheduled container task);
the MVP has no in-process scheduler on purpose, because a background thread inside the API
would run once per replica and quietly multiply the work.

Retention matters beyond disk: a conversation with a tutor can contain whatever a user typed,
and keeping it after the session is gone serves nobody (see LGPD in the README's next steps).
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory
from app.repositories.chat import ChatRepository

logger = get_logger("scripts.cleanup")


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_format == "json")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        removed = await ChatRepository(session).delete_expired_sessions()
        await session.commit()

    await engine.dispose()

    logger.info("cleanup_finished", sessions_removed=removed)
    print(f"Sessoes expiradas removidas: {removed}")


if __name__ == "__main__":
    asyncio.run(main())
