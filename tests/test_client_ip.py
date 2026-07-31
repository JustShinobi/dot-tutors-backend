"""Client address resolution behind a reverse proxy.

Every per-IP limit depends on this, and it fails in both directions: trusting a forgeable header
turns the limits into decoration, ignoring a real proxy collapses every client into one bucket.
"""

from __future__ import annotations

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from app.api.client_ip import UNKNOWN_CLIENT, client_ip


def _request(*, peer: str | None, forwarded: str | None = None) -> Request:
    raw = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw,
        "client": (peer, 12345) if peer else None,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "root_path": "",
        "app": None,
    }
    request = Request(scope)  # type: ignore[arg-type]
    assert request.headers == Headers(raw=[(k, v) for k, v in raw])
    return request


def test_without_a_proxy_the_forwarded_header_is_ignored() -> None:
    """It is client-supplied: honouring it would hand out a fresh bucket per request."""
    request = _request(peer="198.51.100.7", forwarded="1.2.3.4")

    assert client_ip(request, trusted_proxy_hops=0) == "198.51.100.7"


def test_with_one_proxy_the_client_entry_is_used() -> None:
    """A single proxy appends the address it received from — the client — and nothing else.

    (nginx's `$proxy_add_x_forwarded_for` is "$http_x_forwarded_for, $remote_addr", so the proxy
    never adds *itself*; it adds its peer.)
    """
    request = _request(peer="10.0.0.1", forwarded="203.0.113.9")

    assert client_ip(request, trusted_proxy_hops=1) == "203.0.113.9"


def test_a_forged_prefix_cannot_reach_past_the_trusted_hops() -> None:
    """An attacker prepends entries; only the rightmost was appended by our own proxy.

    The client sent `X-Forwarded-For: 9.9.9.9, 8.8.8.8`; the proxy appended the address it
    actually saw. Counting from the right is what makes the forged prefix irrelevant.
    """
    request = _request(peer="10.0.0.1", forwarded="9.9.9.9, 8.8.8.8, 203.0.113.9")

    assert client_ip(request, trusted_proxy_hops=1) == "203.0.113.9"


def test_two_proxies_step_two_entries_from_the_right() -> None:
    """Client → CDN → nginx → app: the CDN appended the client, nginx appended the CDN."""
    request = _request(peer="10.0.0.2", forwarded="203.0.113.9, 10.0.0.1")

    assert client_ip(request, trusted_proxy_hops=2) == "203.0.113.9"


def test_a_chain_shorter_than_configured_falls_back_to_the_leftmost() -> None:
    """Misconfiguration must not index out of the list and must not pick a proxy address."""
    request = _request(peer="10.0.0.1", forwarded="203.0.113.9")

    assert client_ip(request, trusted_proxy_hops=3) == "203.0.113.9"


def test_a_configured_proxy_that_sends_no_header_falls_back_to_the_peer() -> None:
    request = _request(peer="10.0.0.1")

    assert client_ip(request, trusted_proxy_hops=1) == "10.0.0.1"


@pytest.mark.parametrize("hops", [0, 1])
def test_a_request_without_a_peer_groups_under_one_key(hops: int) -> None:
    """A stable placeholder groups the unidentifiable together instead of exempting each one."""
    request = _request(peer=None)

    assert client_ip(request, trusted_proxy_hops=hops) == UNKNOWN_CLIENT
