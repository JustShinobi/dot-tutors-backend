"""Retry policy for model calls.

A transient failure from the provider — a 429, a 503, a dropped connection — used to surface to
the user as "nao foi possivel gerar a resposta". Those are exactly the failures worth retrying,
and the ones a user cannot act on.

Three rules keep this from making things worse:

* **Only transient statuses.** A 400 (malformed request) or a 401 (bad key) will fail again
  identically; retrying them just multiplies latency and cost.
* **Exponential backoff with jitter.** Without jitter, every session that fails at the same
  moment retries at the same moment, and the recovering provider is hit by the same spike that
  knocked it over.
* **The total agent timeout still wins.** Retries live *inside* it, never extend it.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_BASE_DELAY_SECONDS = 0.5
_MAX_DELAY_SECONDS = 8.0


def is_retryable(error: BaseException) -> bool:
    """True when trying the same call again could plausibly succeed."""
    if isinstance(error, httpx.TimeoutException | httpx.NetworkError):
        return True

    status = _status_of(error)
    return status is not None and status in RETRYABLE_STATUS_CODES


def _status_of(error: BaseException) -> int | None:
    """Dig a status code out of whichever exception shape the provider raised.

    Each SDK wraps HTTP failures differently, and the agent frameworks wrap those again, so this
    checks the shapes rather than the classes. Guessing wrong only means *not* retrying, which
    is the safe direction.
    """
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code

    for attribute in ("status_code", "code", "http_status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value

    cause = error.__cause__
    return _status_of(cause) if cause is not None else None


def backoff_delay(attempt: int, *, jitter: Callable[[], float] = random.random) -> float:
    """Delay before `attempt` (1-based), capped and jittered."""
    exponential = min(_BASE_DELAY_SECONDS * (2 ** (attempt - 1)), _MAX_DELAY_SECONDS)
    # Full jitter: a uniform draw over the whole window, which spreads a thundering herd better
    # than adding a small random fraction to a fixed delay.
    return float(exponential * jitter())


async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Run `operation`, retrying transient failures with exponential backoff."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as error:
            if attempt >= max_attempts or not is_retryable(error):
                raise

            delay = backoff_delay(attempt)
            if on_retry is not None:
                on_retry(attempt, error, delay)
            else:
                logger.warning(
                    "llm_call_retrying",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    delay_seconds=round(delay, 2),
                    error_type=type(error).__name__,
                )
            await asyncio.sleep(delay)

    # Unreachable: the loop either returns or re-raises on the final attempt.
    msg = "with_retry esgotou as tentativas sem resultado nem excecao"
    raise AssertionError(msg)
