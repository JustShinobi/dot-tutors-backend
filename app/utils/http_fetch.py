"""Guarded HTTP fetcher for knowledge sources.

An administrator configures arbitrary URLs and the agent fetches them at conversation time.
That is a server-side request forgery primitive unless it is fenced in, because the backend
often sits inside a private network next to a cloud metadata endpoint.

The fence has four parts:

* **scheme allowlist** — only `http` and `https`;
* **address blocking** — the resolved IPs must all be public (loopback, private ranges,
  link-local and the cloud metadata address are refused), re-checked on every redirect;
* **content-type allowlist** — text, markdown, HTML or JSON, converted to plain text;
* **hard limits** — timeout, redirect count and a byte ceiling enforced *while streaming*, so a
  hostile endpoint cannot exhaust memory by never ending its response.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Final

import httpx
from selectolax.parser import HTMLParser

from app.core.errors import SourceFetchError
from app.core.logging import get_logger

logger = get_logger(__name__)

_ALLOWED_SCHEMES: Final = frozenset({"http", "https"})
_MAX_REDIRECTS: Final = 3

_TEXT_TYPES: Final = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv"})
_HTML_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
_JSON_TYPES: Final = frozenset({"application/json", "text/json", "application/ld+json"})


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    text: str
    byte_size: int
    content_type: str
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class UnsafeUrlError(SourceFetchError):
    """The URL points somewhere the backend must never reach on a user's behalf."""

    code = "SOURCE_URL_BLOCKED"
    message = "A URL informada nao e permitida."


def assert_public_url(url: str, *, allow_private_network: bool = False) -> None:
    """Reject a URL whose host resolves to a non-public address.

    Resolution happens here *and* is repeated for every redirect hop, because a benign-looking
    public URL can redirect straight to `169.254.169.254`.
    """
    parsed = httpx.URL(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        msg = f"Esquema nao permitido: {parsed.scheme!r}. Use http ou https."
        raise UnsafeUrlError(msg)

    host = parsed.host
    if not host:
        raise UnsafeUrlError("URL sem host.")

    if allow_private_network:
        return

    for address in _resolve(host):
        if not _is_public(address):
            logger.warning("source_url_blocked", host=host, reason="non_public_address")
            raise UnsafeUrlError(
                "A URL aponta para um endereco de rede interna, o que nao e permitido."
            )


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError("Nao foi possivel resolver o endereco da URL.") from exc

    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:  # pragma: no cover - getaddrinfo returned something unexpected
            continue

    if not addresses:
        raise UnsafeUrlError("Nao foi possivel resolver o endereco da URL.")
    return addresses


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local  # covers 169.254.169.254, the cloud metadata endpoint
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def fetch_text(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_bytes: int,
    timeout_seconds: float,
    etag: str | None = None,
    last_modified: str | None = None,
    allow_private_network: bool = False,
) -> FetchedDocument:
    """Download a source and return it as plain text.

    Passing `etag`/`last_modified` turns the call into a conditional request: an unchanged
    document answers `304` and costs almost nothing, which is what keeps the cache cheap.
    """
    assert_public_url(url, allow_private_network=allow_private_network)

    headers = {"Accept": "text/plain, text/markdown, text/html, application/json;q=0.9"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    current_url = url
    for hop in range(_MAX_REDIRECTS + 1):
        response = await _request(
            current_url, client=client, headers=headers, timeout_seconds=timeout_seconds
        )

        # `has_redirect_location`, not `is_redirect`: the latter is true for any 3xx, which
        # includes 304 Not Modified -- a cache hit, not a redirect.
        if response.has_redirect_location:
            location = response.headers["location"]
            current_url = str(httpx.URL(current_url).join(location))
            # Re-validate: the first URL being public says nothing about where it points.
            assert_public_url(current_url, allow_private_network=allow_private_network)
            await response.aclose()
            if hop == _MAX_REDIRECTS:
                raise SourceFetchError("Excesso de redirecionamentos.")
            continue

        return await _read_document(response, url=current_url, max_bytes=max_bytes)

    raise SourceFetchError("Excesso de redirecionamentos.")  # pragma: no cover - loop exits above


async def _request(
    url: str, *, client: httpx.AsyncClient, headers: dict[str, str], timeout_seconds: float
) -> httpx.Response:
    request = client.build_request("GET", url, headers=headers, timeout=timeout_seconds)
    try:
        return await client.send(request, stream=True, follow_redirects=False)
    except httpx.TimeoutException as exc:
        raise SourceFetchError("A fonte demorou demais para responder.") from exc
    except httpx.HTTPError as exc:
        raise SourceFetchError("Nao foi possivel acessar a fonte.") from exc


async def _read_document(response: httpx.Response, *, url: str, max_bytes: int) -> FetchedDocument:
    try:
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return FetchedDocument(
                text="",
                byte_size=0,
                content_type=_content_type(response),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                not_modified=True,
            )

        if response.status_code >= httpx.codes.BAD_REQUEST:
            logger.warning(
                "source_fetch_http_error",
                status_code=response.status_code,
                host=httpx.URL(url).host,
            )
            raise SourceFetchError(
                f"A fonte respondeu com status {response.status_code}.",
            )

        content_type = _content_type(response)
        raw = await _read_limited(response, max_bytes=max_bytes)
        encoding = response.encoding or "utf-8"
        body = raw.decode(encoding, errors="replace")

        return FetchedDocument(
            text=_to_plain_text(body, content_type=content_type),
            byte_size=len(raw),
            content_type=content_type,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )
    finally:
        await response.aclose()


async def _read_limited(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Stop reading at `max_bytes` instead of trusting `Content-Length`.

    A hostile or misconfigured endpoint can omit or lie about the header, so the ceiling is
    applied to the bytes actually received.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total >= max_bytes:
            chunks.append(chunk[: max_bytes - (total - len(chunk))])
            logger.info("source_fetch_truncated", max_bytes=max_bytes)
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _content_type(response: httpx.Response) -> str:
    raw: str = response.headers.get("content-type", "")
    return raw.split(";")[0].strip().lower()


def _to_plain_text(body: str, *, content_type: str) -> str:
    if content_type in _HTML_TYPES:
        return _html_to_text(body)
    if content_type in _JSON_TYPES:
        return _json_to_text(body)
    if content_type in _TEXT_TYPES or content_type == "":
        return body
    raise SourceFetchError(
        f"Tipo de conteudo nao suportado: {content_type or 'desconhecido'}. "
        "Use texto, markdown, HTML ou JSON.",
    )


def _html_to_text(body: str) -> str:
    tree = HTMLParser(body)
    for tag in ("script", "style", "noscript", "svg", "nav", "footer"):
        for node in tree.css(tag):
            node.decompose()

    root = tree.css_first("main") or tree.css_first("article") or tree.body
    if root is None:
        return ""

    text = root.text(separator="\n", strip=True)
    # Collapse the runs of blank lines that stripping tags leaves behind.
    lines = [line.strip() for line in text.splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def _json_to_text(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceFetchError("A fonte declarou JSON mas o conteudo e invalido.") from exc
    return json.dumps(parsed, ensure_ascii=False, indent=2)
