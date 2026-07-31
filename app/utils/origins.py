"""Origin parsing and comparison.

The `Origin` header is what actually authorises an embed (PRD 3.4), so comparing it has to be
exact and boring: no substring matching, no suffix matching, no "startswith". Those are the
classic ways an allowlist gets bypassed (`https://cliente.com.attacker.net` passing a `endswith`
check, for instance).

An origin is the triple *scheme + host + port*. Everything else in a URL — path, query, trailing
slash, credentials — is not part of it and is rejected rather than silently ignored.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}
_ALLOWED_SCHEMES = frozenset(_DEFAULT_PORTS)


class InvalidOriginError(ValueError):
    """Raised when a string cannot be interpreted as a browser origin."""


def normalize_origin(value: str) -> str:
    """Return the canonical `scheme://host[:port]` form of an origin.

    Normalising both sides before comparing is what makes `https://Cliente.com:443/` and
    `https://cliente.com` match — while keeping `http://` and `https://` distinct, since they
    are genuinely different origins to a browser.
    """
    candidate = value.strip()
    if not candidate:
        msg = "Origem vazia."
        raise InvalidOriginError(msg)

    parts = urlsplit(candidate)

    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        msg = f"Origem deve usar http ou https: {value!r}"
        raise InvalidOriginError(msg)

    if parts.username or parts.password:
        msg = f"Origem nao pode conter credenciais: {value!r}"
        raise InvalidOriginError(msg)

    if parts.path not in ("", "/") or parts.query or parts.fragment:
        msg = f"Origem nao pode conter caminho, query ou fragmento: {value!r}"
        raise InvalidOriginError(msg)

    host = (parts.hostname or "").lower()
    if not host:
        msg = f"Origem sem host: {value!r}"
        raise InvalidOriginError(msg)

    try:
        port = parts.port
    except ValueError as exc:  # malformed port, e.g. "https://a:porta"
        msg = f"Porta invalida na origem: {value!r}"
        raise InvalidOriginError(msg) from exc

    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def origin_matches(candidate: str | None, allowlist: list[str]) -> bool:
    """Exact membership test against an allowlist.

    An empty allowlist means "any origin" — only acceptable in local development, and the API
    refuses to create such a key outside it.
    """
    if not allowlist:
        return True
    if not candidate:
        return False

    try:
        normalized = normalize_origin(candidate)
    except InvalidOriginError:
        return False

    return any(normalized == entry for entry in _normalize_all_quietly(allowlist))


def normalize_all(values: list[str]) -> list[str]:
    """Normalise and de-duplicate an allowlist, preserving the order it was declared in."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(normalize_origin(value), None)
    return list(seen)


def _normalize_all_quietly(values: list[str]) -> list[str]:
    """Same as `normalize_all`, but skips entries that are already invalid.

    Stored allowlists are normalised on write, so this only guards against rows written by an
    older version — a bad entry must never make the comparison crash at request time.
    """
    normalized: list[str] = []
    for value in values:
        try:
            normalized.append(normalize_origin(value))
        except InvalidOriginError:
            continue
    return normalized
