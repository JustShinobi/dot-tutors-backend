"""Domain error taxonomy.

Services raise these instead of `HTTPException` so business rules stay independent of the HTTP
layer. `app/api` maps them to responses; the exception handlers guarantee that no stack trace
ever reaches a client (PRD 5.1).
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, client-facing failures.

    `code` is a stable machine-readable identifier; `message` is safe to show to a user and must
    never contain internals (queries, file paths, upstream payloads).
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "Erro interno."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    message = "Recurso nao encontrado."


class ConflictError(AppError):
    code = "CONFLICT"
    status_code = 409
    message = "Conflito com o estado atual do recurso."


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    message = "Dados invalidos."


class AuthenticationError(AppError):
    code = "UNAUTHENTICATED"
    status_code = 401
    message = "Credenciais invalidas ou ausentes."


class AuthorizationError(AppError):
    code = "FORBIDDEN"
    status_code = 403
    message = "Acesso negado."


class RateLimitError(AppError):
    code = "RATE_LIMITED"
    status_code = 429
    message = "Muitas requisicoes. Tente novamente em instantes."

    def __init__(self, retry_after_seconds: int = 60, message: str | None = None) -> None:
        super().__init__(message, details={"retry_after_seconds": retry_after_seconds})
        self.retry_after_seconds = retry_after_seconds


# --- domain-specific -------------------------------------------------------


class TutorNotFoundError(NotFoundError):
    code = "TUTOR_NOT_FOUND"
    message = "Tutor nao encontrado."


class TutorInactiveError(ConflictError):
    code = "TUTOR_INACTIVE"
    message = "Este tutor esta desativado no momento."


class SlugAlreadyUsedError(ConflictError):
    code = "TUTOR_SLUG_TAKEN"
    message = "Ja existe um tutor com este identificador."


class SourceNotFoundError(NotFoundError):
    code = "SOURCE_NOT_FOUND"
    message = "Fonte de conhecimento nao encontrada."


class SourceLimitReachedError(ConflictError):
    code = "SOURCE_LIMIT_REACHED"
    message = "Limite de fontes por tutor atingido."


class SourceFetchError(AppError):
    code = "SOURCE_FETCH_FAILED"
    status_code = 502
    message = "Nao foi possivel obter o conteudo da fonte."


class EmbedKeyNotFoundError(NotFoundError):
    code = "EMBED_KEY_NOT_FOUND"
    message = "Chave de embed invalida."


class EmbedKeyRevokedError(AuthenticationError):
    code = "EMBED_KEY_REVOKED"
    message = "Chave de embed revogada."


class OriginNotAllowedError(AuthorizationError):
    code = "ORIGIN_NOT_ALLOWED"
    message = "Este site nao esta autorizado a carregar o tutor."


class SessionExpiredError(AuthenticationError):
    code = "SESSION_EXPIRED"
    message = "Sessao expirada. Recarregue o widget."


class AgentTimeoutError(AppError):
    code = "AGENT_TIMEOUT"
    status_code = 504
    message = "O tutor demorou demais para responder. Tente novamente."


class AgentExecutionError(AppError):
    code = "AGENT_FAILED"
    status_code = 502
    message = "Nao foi possivel gerar a resposta do tutor."
