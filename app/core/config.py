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
    seed_admin_email: str = "admin@dot.local"
    seed_admin_password: str = _PLACEHOLDER_ADMIN_PASSWORD

    # --- llm ---------------------------------------------------------------
    llm_provider: str = "google"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: str = ""

    # --- agent -------------------------------------------------------------
    agent_runner: AgentRunnerName = "pydantic_ai"
    agent_max_tool_calls: int = 6
    agent_timeout_seconds: int = 45

    # --- knowledge sources -------------------------------------------------
    history_max_messages: int = 20
    source_max_bytes: int = 512_000
    source_cache_ttl_minutes: int = 60
    source_fetch_timeout_seconds: int = 10
    source_allow_private_network: bool = False

    # --- cors / embed ------------------------------------------------------
    admin_origin: str = "http://localhost:3000"
    embed_default_origins: str = "http://localhost:3000"

    # --- rate limit --------------------------------------------------------
    rate_limit_chat_per_minute: int = 20
    rate_limit_session_per_minute: int = 10

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


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance. Call `get_settings.cache_clear()` in tests."""
    return Settings()
