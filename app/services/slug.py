"""Slug generation.

The slug is the human-readable identifier of a tutor and appears in admin URLs, so it has to be
stable, unique and safe to place in a path segment.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_EDGE_DASHES = re.compile(r"^-+|-+$")

MAX_SLUG_LENGTH = 140


def normalize_slug(value: str) -> str:
    """Turn arbitrary text into a lowercase, dash-separated ASCII slug.

    Accents are folded rather than dropped ("Tutor de Matemática" -> "tutor-de-matematica") so
    Portuguese titles produce readable slugs.
    """
    folded = unicodedata.normalize("NFKD", value)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = _EDGE_DASHES.sub("", _NON_SLUG.sub("-", ascii_only))[:MAX_SLUG_LENGTH]
    return _EDGE_DASHES.sub("", slug)


async def generate_unique_slug(
    base: str,
    *,
    exists: Callable[[str], Awaitable[bool]],
    max_attempts: int = 50,
) -> str:
    """Return a slug derived from `base` that no other tutor uses.

    Collisions get a numeric suffix (`tutor-de-ingles-2`). `exists` is injected so this stays a
    pure function of its inputs and is trivial to test without a database.
    """
    slug = normalize_slug(base) or "tutor"

    if not await exists(slug):
        return slug

    for suffix in range(2, max_attempts + 2):
        candidate = f"{slug[: MAX_SLUG_LENGTH - len(str(suffix)) - 1]}-{suffix}"
        if not await exists(candidate):
            return candidate

    msg = f"could not derive a unique slug from {base!r} after {max_attempts} attempts"
    raise RuntimeError(msg)
