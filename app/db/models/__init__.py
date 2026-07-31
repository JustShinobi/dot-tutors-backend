"""ORM models.

Every model is imported here so that `Base.metadata` is complete for Alembic autogenerate and
for `create_all` in tests.
"""

from app.db.models.admin import AdminRole, AdminUser
from app.db.models.chat import ChatMessage, ChatSession, MessageRole
from app.db.models.embed import EmbedKey
from app.db.models.source_cache import SourceDocument
from app.db.models.tutor import SourceKind, Tutor, TutorSource, TutorStatus

__all__ = [
    "AdminRole",
    "AdminUser",
    "ChatMessage",
    "ChatSession",
    "EmbedKey",
    "MessageRole",
    "SourceDocument",
    "SourceKind",
    "Tutor",
    "TutorSource",
    "TutorStatus",
]
