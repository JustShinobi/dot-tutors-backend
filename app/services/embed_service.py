"""Embed key management and authorisation (PRD 3.2 and 3.4).

The security model in one paragraph: the embed key is **public**. It travels in the `src` of the
integrator's `<iframe>` and anyone can read it in the page source. Treating it as a secret would
be self-deception. What actually protects a tutor is the combination of

1. the key being bound to an **allowlist of origins**, checked against the browser-sent `Origin`
   header on every session request;
2. the session token issued afterwards being **short-lived and scoped** to one session;
3. rate limiting;
4. the real secret — the LLM API key — never leaving the backend.
"""

from __future__ import annotations

import secrets

from app.core.config import Settings
from app.core.errors import (
    EmbedKeyNotFoundError,
    EmbedKeyRevokedError,
    OriginNotAllowedError,
    TutorInactiveError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models.embed import EmbedKey
from app.db.models.tutor import Tutor
from app.repositories.embed import EmbedKeyRepository
from app.schemas.embed import EmbedKeyCreate, EmbedSnippet
from app.services.tutor_service import TutorService
from app.utils.origins import origin_matches

logger = get_logger(__name__)

PUBLIC_KEY_PREFIX = "pk_live_"
_PUBLIC_KEY_BYTES = 24
"""192 bits of entropy: not a secret, but large enough that keys cannot be guessed or scanned."""


def generate_public_key() -> str:
    return f"{PUBLIC_KEY_PREFIX}{secrets.token_urlsafe(_PUBLIC_KEY_BYTES)}"


class EmbedService:
    def __init__(
        self,
        *,
        keys: EmbedKeyRepository,
        tutors: TutorService,
        settings: Settings,
    ) -> None:
        self._keys = keys
        self._tutors = tutors
        self._settings = settings

    # --- management --------------------------------------------------------

    async def create_key(self, tutor_id: str, payload: EmbedKeyCreate) -> EmbedKey:
        tutor = await self._tutors.get(tutor_id)

        origins = payload.allowed_origins or self._settings.default_embed_origins
        if not origins and self._settings.app_env not in ("local", "test"):
            raise ValidationError(
                "Informe ao menos uma origem permitida: chave sem allowlist so e aceita em "
                "ambiente local.",
                code="EMBED_ORIGINS_REQUIRED",
            )

        key = EmbedKey(
            tutor_id=tutor.id,
            public_key=await self._unique_public_key(),
            label=payload.label.strip(),
            allowed_origins=list(origins),
            is_active=True,
        )
        self._keys.add(key)
        await self._keys.flush()

        logger.info(
            "embed_key_created",
            tutor_id=tutor.id,
            embed_key_id=key.id,
            origin_count=len(key.allowed_origins),
            allows_any_origin=key.allows_any_origin,
        )
        return key

    async def list_keys(self, tutor_id: str) -> list[EmbedKey]:
        tutor = await self._tutors.get(tutor_id)
        return await self._keys.list_for_tutor(tutor.id)

    async def revoke_key(self, key_id: str) -> EmbedKey:
        key = await self._keys.get(key_id)
        if key is None:
            raise EmbedKeyNotFoundError

        if key.is_active:
            key.is_active = False
            key.revoked_at = utcnow()
            await self._keys.flush()
            logger.info("embed_key_revoked", embed_key_id=key.id, tutor_id=key.tutor_id)
        return key

    async def build_snippet(self, tutor_id: str, key_id: str) -> EmbedSnippet:
        """Produce the copy-and-paste integration snippet (PRD 3.2)."""
        tutor = await self._tutors.get(tutor_id)
        key = await self._keys.get(key_id)
        if key is None or key.tutor_id != tutor.id:
            raise EmbedKeyNotFoundError

        embed_url = f"{self._settings.frontend_base_url.rstrip('/')}/embed/{key.public_key}"
        iframe_html = (
            f"<iframe\n"
            f'  src="{embed_url}"\n'
            f'  title="Tutor: {tutor.title}"\n'
            f'  width="400"\n'
            f'  height="620"\n'
            f'  style="border:0;border-radius:12px"\n'
            f'  loading="lazy"\n'
            f'  referrerpolicy="strict-origin-when-cross-origin"\n'
            f"></iframe>"
        )

        notes = [
            "Esta chave e publica: ela aparece no HTML do seu site e nao deve ser tratada como "
            "segredo.",
            "O acesso e autorizado pela lista de origens permitidas desta chave. Adicione o "
            "dominio do seu site, senao o widget recusa a conexao.",
            "Nenhuma credencial do modelo de linguagem trafega pelo navegador.",
        ]
        if key.allows_any_origin:
            notes.append(
                "ATENCAO: esta chave aceita qualquer origem. Use apenas em desenvolvimento."
            )
        if not key.is_active:
            notes.append("ATENCAO: esta chave esta revogada e nao abre novas sessoes.")

        return EmbedSnippet(
            tutor_id=tutor.id,
            tutor_title=tutor.title,
            public_key=key.public_key,
            embed_url=embed_url,
            iframe_html=iframe_html,
            allowed_origins=list(key.allowed_origins),
            notes=notes,
        )

    async def describe_key(self, public_key: str) -> EmbedKey:
        """Look up a key without opening a session.

        Used to build the widget page's framing policy. It answers for revoked keys too: the
        page must still refuse to be framed, and hiding that would only turn a clear error into
        a blank iframe.
        """
        key = await self._keys.get_by_public_key(public_key)
        if key is None:
            raise EmbedKeyNotFoundError
        return key

    # --- runtime authorisation --------------------------------------------

    async def authorize(self, public_key: str, origin: str | None) -> tuple[EmbedKey, Tutor]:
        """Validate a key and the requesting origin before any session is opened.

        Ordering matters: an unknown key and a revoked key are told apart because the
        integrator needs actionable feedback, but neither reveals whether the *tutor* exists.
        """
        key = await self._keys.get_by_public_key(public_key)
        if key is None:
            logger.warning("embed_key_unknown", origin=origin)
            raise EmbedKeyNotFoundError

        if not key.is_active:
            logger.warning("embed_key_revoked_use", embed_key_id=key.id, origin=origin)
            raise EmbedKeyRevokedError

        if not origin_matches(origin, key.allowed_origins):
            logger.warning(
                "embed_origin_rejected",
                embed_key_id=key.id,
                tutor_id=key.tutor_id,
                origin=origin,
            )
            raise OriginNotAllowedError

        tutor = key.tutor
        if not tutor.is_active:
            logger.info("embed_tutor_inactive", tutor_id=tutor.id, embed_key_id=key.id)
            raise TutorInactiveError

        key.last_used_at = utcnow()
        return key, tutor

    # --- helpers -----------------------------------------------------------

    async def _unique_public_key(self) -> str:
        for _ in range(5):
            candidate = generate_public_key()
            if not await self._keys.public_key_exists(candidate):
                return candidate
        msg = "could not generate a unique public embed key"
        raise RuntimeError(msg)
