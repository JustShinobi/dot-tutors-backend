"""The guarded fetcher, with emphasis on the SSRF fence."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.errors import SourceFetchError
from app.utils.http_fetch import UnsafeUrlError, assert_public_url, fetch_text

PUBLIC_URL = "https://exemplo-publico.test/doc.md"


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient() as async_client:
        yield async_client


# --- SSRF fence ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/internal",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
    ids=[
        "loopback-ipv4",
        "localhost",
        "metadata-de-nuvem",
        "rede-privada-10",
        "rede-privada-192",
        "rede-privada-172",
        "loopback-ipv6",
        "endereco-nao-especificado",
    ],
)
def test_non_public_addresses_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://servidor/arquivo", "gopher://a/b", "data:text/plain,oi"],
)
def test_non_http_schemes_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


def test_the_fence_can_be_opened_explicitly_for_local_development() -> None:
    assert_public_url("http://127.0.0.1:9999/doc.md", allow_private_network=True)


# --- fetching --------------------------------------------------------------


@respx.mock
async def test_fetch_plain_text(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, text="# Titulo\n\nConteudo.", headers={"content-type": "text/markdown"}
        )
    )

    document = await fetch_text(
        PUBLIC_URL,
        client=client,
        max_bytes=10_000,
        timeout_seconds=5,
        allow_private_network=True,
    )

    assert "Conteudo." in document.text
    assert document.byte_size > 0


@respx.mock
async def test_html_is_reduced_to_readable_text(client: httpx.AsyncClient) -> None:
    html = """
    <html><body>
      <nav>menu que nao interessa</nav>
      <main><h1>Politica</h1><p>O auxilio e de R$ 150.</p></main>
      <script>alert('xss')</script>
    </body></html>
    """
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    document = await fetch_text(
        PUBLIC_URL,
        client=client,
        max_bytes=10_000,
        timeout_seconds=5,
        allow_private_network=True,
    )

    assert "R$ 150" in document.text
    assert "alert" not in document.text
    assert "menu que nao interessa" not in document.text


@respx.mock
async def test_json_is_pretty_printed(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, text='{"prazo":30}', headers={"content-type": "application/json"}
        )
    )

    document = await fetch_text(
        PUBLIC_URL,
        client=client,
        max_bytes=10_000,
        timeout_seconds=5,
        allow_private_network=True,
    )

    assert '"prazo": 30' in document.text


@respx.mock
async def test_an_unsupported_content_type_is_refused(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
        )
    )

    with pytest.raises(SourceFetchError, match="Tipo de conteudo"):
        await fetch_text(
            PUBLIC_URL,
            client=client,
            max_bytes=10_000,
            timeout_seconds=5,
            allow_private_network=True,
        )


@respx.mock
async def test_the_byte_ceiling_is_enforced_while_streaming(client: httpx.AsyncClient) -> None:
    """A hostile endpoint can lie about Content-Length, so the limit applies to bytes read."""
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, text="A" * 50_000, headers={"content-type": "text/plain", "content-length": "10"}
        )
    )

    document = await fetch_text(
        PUBLIC_URL,
        client=client,
        max_bytes=1_000,
        timeout_seconds=5,
        allow_private_network=True,
    )

    assert document.byte_size <= 1_000


@respx.mock
async def test_a_304_is_reported_as_not_modified(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(return_value=httpx.Response(304, headers={"etag": '"v1"'}))

    document = await fetch_text(
        PUBLIC_URL,
        client=client,
        max_bytes=10_000,
        timeout_seconds=5,
        etag='"v1"',
        allow_private_network=True,
    )

    assert document.not_modified is True
    assert document.etag == '"v1"'


@respx.mock
async def test_an_http_error_becomes_a_domain_error(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(SourceFetchError, match="status 503"):
        await fetch_text(
            PUBLIC_URL,
            client=client,
            max_bytes=10_000,
            timeout_seconds=5,
            allow_private_network=True,
        )


@respx.mock
async def test_a_timeout_becomes_a_domain_error(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))

    with pytest.raises(SourceFetchError, match="demorou demais"):
        await fetch_text(
            PUBLIC_URL,
            client=client,
            max_bytes=10_000,
            timeout_seconds=1,
            allow_private_network=True,
        )


@respx.mock
async def test_a_redirect_to_a_private_address_is_blocked(client: httpx.AsyncClient) -> None:
    """A public URL says nothing about where it points; every hop is re-checked."""
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
    )

    with pytest.raises(UnsafeUrlError):
        await fetch_text(
            PUBLIC_URL,
            client=client,
            max_bytes=10_000,
            timeout_seconds=5,
            allow_private_network=False,
        )


@respx.mock
async def test_a_redirect_chain_is_bounded(client: httpx.AsyncClient) -> None:
    respx.get(PUBLIC_URL).mock(return_value=httpx.Response(302, headers={"location": PUBLIC_URL}))

    with pytest.raises(SourceFetchError, match="redirecionamentos"):
        await fetch_text(
            PUBLIC_URL,
            client=client,
            max_bytes=10_000,
            timeout_seconds=5,
            allow_private_network=True,
        )
