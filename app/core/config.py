"""Application settings, loaded from environment variables.

Every secret and environment-specific value enters the application through this module
(PRD 4.3.3): nothing is hardcoded and nothing is read from the environment elsewhere.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]
LogFormat = Literal["console", "json"]
AgentRunnerName = Literal["pydantic_ai", "langgraph"]

_PLACEHOLDER_JWT_SECRET = "insecure-development-secret-change-me"  # noqa: S105
_PLACEHOLDER_ADMIN_PASSWORD = "change-me"  # noqa: S105


class Settings(BaseSettings):
    """Typed view over the environment. See `.env.example` for documentation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -------------------------------------------------------
    app_env: AppEnv = "local"
    debug: bool = False
    log_level: str = "INFO"
    log_format: LogFormat = "console"

    # --- database ----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./dot_tutors.db"

    # --- auth --------------------------------------------------------------
    # Deliberately invalid placeholders, not credentials: the application refuses to start with
    # them outside `local`/`test` (see `_reject_placeholders`).
    jwt_secret: str = _PLACEHOLDER_JWT_SECRET
    jwt_algorithm: str = "HS256"
    admin_access_token_ttl_minutes: int = 30
    embed_session_ttl_minutes: int = 30
    seed_admin_email: str = "admin@example.com"
    seed_admin_password: str = _PLACEHOLDER_ADMIN_PASSWORD

    # --- llm ---------------------------------------------------------------
    llm_provider: str = "google"
    llm_model: str = "gemini-3.6-flash"
    gemini_api_key: str = ""

    # --- agent -------------------------------------------------------------
    agent_runner: AgentRunnerName = "pydantic_ai"
    agent_max_tool_calls: int = 6
    agent_timeout_seconds: int = 45
    agent_max_attempts: int = 3
    """Total attempts per message, including the first. Retries happen inside the timeout."""

    # --- knowledge sources -------------------------------------------------
    history_max_messages: int = 20
    source_max_bytes: int = 512_000
    source_cache_ttl_minutes: int = 60
    source_fetch_timeout_seconds: int = 10
    source_allow_private_network: bool = False

    # --- cors / embed ------------------------------------------------------
    admin_origin: str = "http://localhost:3000"
    embed_default_origins: str = "http://localhost:3000"
    frontend_base_url: str = "http://localhost:3000"
    """Public URL of the frontend, used to build the `<iframe>` snippet handed to integrators."""

    # --- rate limit --------------------------------------------------------
    rate_limit_chat_per_minute: int = 20
    rate_limit_session_per_minute: int = 10
    rate_limit_chat_per_ip_per_minute: int = 60
    """Ceiling per (embed key, IP). The per-session limit alone is trivially bypassed by
    opening new sessions, so the two work together."""
    rate_limit_login_per_minute: int = 5
    """The admin login is the only password-checking endpoint; without this it is an open
    brute-force target."""

    # --- deployment --------------------------------------------------------
    trusted_proxy_hops: int = 0
    """How many reverse proxies sit in front of the API.

    `0` means the app is reached directly and `X-Forwarded-For` must be ignored — trusting it
    then would let any client forge its own IP and defeat every per-IP limit. Behind one proxy
    (nginx, Traefik, Caddy) set `1`.
    """

    expose_api_docs: bool | None = None
    """`None` = enabled only outside staging/production. Set explicitly to publish `/docs`
    on a deployed demo."""

    # --- observability -----------------------------------------------------
    logfire_token: str = ""

    # --- domain limits (not environment-tunable on purpose) ----------------
    max_message_chars: int = Field(default=2_000, exclude=True)
    max_instructions_chars: int = Field(default=8_000, exclude=True)
    max_sources_per_tutor: int = Field(default=10, exclude=True)

    @model_validator(mode="after")
    def _reject_placeholders(self) -> Settings:
        """Refuse to boot a real environment with the example credentials.

        The placeholders exist so `local` and `test` run with zero setup; letting them reach
        staging or production would silently ship a known-value JWT signing key.
        """
        if self.app_env in ("local", "test"):
            return self
        placeholders = {
            "JWT_SECRET": self.jwt_secret == _PLACEHOLDER_JWT_SECRET,
            "SEED_ADMIN_PASSWORD": self.seed_admin_password == _PLACEHOLDER_ADMIN_PASSWORD,
        }
        offenders = sorted(name for name, is_placeholder in placeholders.items() if is_placeholder)
        if offenders:
            msg = f"{', '.join(offenders)} still hold the example value in APP_ENV={self.app_env}"
            raise ValueError(msg)
        return self

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            msg = f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}"
            raise ValueError(msg)
        return level

    @property
    def admin_origins(self) -> list[str]:
        """Origins allowed to call the admin API."""
        return _split_csv(self.admin_origin)

    @property
    def default_embed_origins(self) -> list[str]:
        """Origins pre-filled when an embed key is created without an explicit allowlist."""
        return _split_csv(self.embed_default_origins)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def docs_enabled(self) -> bool:
        """Whether to publish `/docs` and `/openapi.json`.

        Off by default in a deployed environment: the schema lists every administrative route
        and its payloads, which is free reconnaissance. A demo that *wants* the interactive docs
        turns them back on explicitly.
        """
        if self.expose_api_docs is not None:
            return self.expose_api_docs
        return self.app_env in ("local", "test")

    @property
    def resolved_log_format(self) -> LogFormat:
        """JSON once deployed, human-readable while developing.

        Structured output only pays off where something ingests it; a colourised console is
        better on a laptop. Setting `LOG_FORMAT` explicitly always wins.
        """
        if "log_format" in self.model_fields_set:
            return self.log_format
        return "json" if self.app_env in ("staging", "production") else "console"


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance. Call `get_settings.cache_clear()` in tests."""
    return Settings()
