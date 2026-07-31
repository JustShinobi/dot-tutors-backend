"""In-process rate limiting (PRD 5.1).

A token bucket kept in memory. This is deliberately the simplest thing that works for a demo,
and its limitation is stated rather than hidden: **it does not survive a restart and is not
shared between replicas**, so a horizontally scaled deployment would multiply the effective
limit by the number of instances. Production would move the counter to Redis; that is listed in
the "next steps" section of the README.

What it does buy, even so: a single abusive page cannot spend the tutor's LLM budget in a loop,
which is the realistic threat for a widget whose key is public by design.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock

from app.core.errors import RateLimitError


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class TokenBucketLimiter:
    """Classic token bucket: `capacity` requests, refilled over `per_seconds`.

    Bursts are allowed up to the capacity, which is what a chat UI needs — a user sending two
    messages quickly is normal, a script sending two hundred is not.
    """

    capacity: int
    per_seconds: float = 60.0
    _buckets: dict[str, _Bucket] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def _refill_rate(self) -> float:
        return self.capacity / self.per_seconds

    def check(self, key: str) -> None:
        """Consume one token for `key`, or raise `RateLimitError`."""
        retry_after = self._consume(key)
        if retry_after is not None:
            raise RateLimitError(retry_after_seconds=retry_after)

    def _consume(self, key: str) -> int | None:
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=self.capacity - 1, updated_at=now)
                return None

            elapsed = now - bucket.updated_at
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self._refill_rate)
            bucket.updated_at = now

            if bucket.tokens < 1:
                missing = 1 - bucket.tokens
                return max(1, int(missing / self._refill_rate) + 1)

            bucket.tokens -= 1
            return None

    def reset(self) -> None:
        """Drop all state. Used by tests; there is no production caller."""
        with self._lock:
            self._buckets.clear()

    def prune(self, *, older_than_seconds: float = 3_600) -> int:
        """Discard idle buckets so the dictionary cannot grow without bound.

        Every distinct key allocates an entry, and the key includes the client IP — without
        this, a long-running process facing many clients would leak memory slowly.
        """
        cutoff = time.monotonic() - older_than_seconds
        with self._lock:
            stale = [key for key, bucket in self._buckets.items() if bucket.updated_at < cutoff]
            for key in stale:
                del self._buckets[key]
        return len(stale)
