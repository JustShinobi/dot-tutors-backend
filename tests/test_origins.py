"""Origin normalisation and matching.

This is the check that decides whether a third-party site may load a tutor, so the tests focus
on the ways an allowlist is usually bypassed rather than on the happy path.
"""

from __future__ import annotations

import pytest

from app.utils.origins import InvalidOriginError, normalize_all, normalize_origin, origin_matches


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://cliente.com", "https://cliente.com"),
        ("https://cliente.com/", "https://cliente.com"),
        ("https://CLIENTE.com", "https://cliente.com"),
        ("https://cliente.com:443", "https://cliente.com"),
        ("http://cliente.com:80", "http://cliente.com"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("https://cliente.com:8443", "https://cliente.com:8443"),
        ("  https://cliente.com  ", "https://cliente.com"),
    ],
)
def test_normalize_origin_canonicalizes(raw: str, expected: str) -> None:
    assert normalize_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "cliente.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
        "https://cliente.com/app",
        "https://cliente.com?a=1",
        "https://user:pass@cliente.com",
        "https://cliente.com:porta",
    ],
    ids=[
        "vazio",
        "sem-esquema",
        "esquema-file",
        "esquema-javascript",
        "sem-host",
        "com-caminho",
        "com-query",
        "com-credenciais",
        "porta-invalida",
    ],
)
def test_normalize_origin_rejects_what_is_not_an_origin(raw: str) -> None:
    with pytest.raises(InvalidOriginError):
        normalize_origin(raw)


def test_http_and_https_are_different_origins() -> None:
    assert normalize_origin("http://cliente.com") != normalize_origin("https://cliente.com")


def test_normalize_all_deduplicates_preserving_order() -> None:
    result = normalize_all(
        ["https://b.com", "https://a.com/", "https://B.com:443", "https://a.com"]
    )

    assert result == ["https://b.com", "https://a.com"]


# --- matching --------------------------------------------------------------


def test_allowed_origin_matches() -> None:
    assert origin_matches("https://cliente.com", ["https://cliente.com"])
    assert origin_matches("https://cliente.com:443/", ["https://cliente.com"])


@pytest.mark.parametrize(
    "attacker",
    [
        "https://cliente.com.attacker.net",
        "https://attacker.net/cliente.com",
        "https://notcliente.com",
        "https://sub.cliente.com",
        "http://cliente.com",
        "https://cliente.com:8443",
        "null",
        "",
    ],
    ids=[
        "sufixo-enganoso",
        "caminho-enganoso",
        "prefixo-enganoso",
        "subdominio-nao-listado",
        "esquema-diferente",
        "porta-diferente",
        "origin-null-de-sandbox",
        "origin-vazio",
    ],
)
def test_lookalike_origins_are_rejected(attacker: str) -> None:
    """Substring, prefix and suffix comparisons are the classic allowlist bypass."""
    assert not origin_matches(attacker, ["https://cliente.com"])


def test_missing_origin_header_is_rejected_when_an_allowlist_exists() -> None:
    assert not origin_matches(None, ["https://cliente.com"])


def test_empty_allowlist_allows_anything() -> None:
    """Documented escape hatch for local development only."""
    assert origin_matches("https://qualquer.com", [])
    assert origin_matches(None, [])


def test_a_corrupt_entry_in_the_allowlist_does_not_crash_the_check() -> None:
    """A row written by an older version must not turn into a request-time exception."""
    assert origin_matches("https://cliente.com", ["nao-e-origem", "https://cliente.com"])
    assert not origin_matches("https://outro.com", ["nao-e-origem"])
