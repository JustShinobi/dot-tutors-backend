"""Resolving the client address behind a reverse proxy.

Every per-IP limit in this application is only as trustworthy as this function. Two failure
modes, in opposite directions:

* **Trusting `X-Forwarded-For` when nothing sets it.** The header is client-supplied. If the app
  is reachable directly, anyone can send `X-Forwarded-For: 1.2.3.4` and get a fresh rate-limit
  bucket per request — the limit becomes decoration.
* **Ignoring it when a proxy *is* in front.** Every request then carries the proxy's address, so
  all clients share one bucket and the first abusive one locks out everybody else.

There is no way to tell those apart by inspection, so it is configuration: `TRUSTED_PROXY_HOPS`
states how many proxies the operator put in front. The address is taken that many positions from
the right of the chain — the right-hand entries are the ones appended by infrastructure the
operator controls; everything to the left came from the client and is forgeable.
"""

from __future__ import annotations

from starlette.requests import Request

FORWARDED_FOR = "x-forwarded-for"

UNKNOWN_CLIENT = "desconhecido"


def client_ip(request: Request, *, trusted_proxy_hops: int = 0) -> str:
    """Best available identifier for the caller.

    Returns `UNKNOWN_CLIENT` when there is no peer address at all (an ASGI transport without a
    socket, as in tests). Callers use the result as a rate-limit key, so a stable placeholder is
    fine — it groups the unidentifiable together rather than granting them a free pass each.
    """
    peer = request.client.host if request.client else None

    if trusted_proxy_hops <= 0:
        return peer or UNKNOWN_CLIENT

    forwarded = request.headers.get(FORWARDED_FOR)
    if not forwarded:
        # Configured for a proxy but none set the header: fall back rather than invent one.
        return peer or UNKNOWN_CLIENT

    chain = [item.strip() for item in forwarded.split(",") if item.strip()]
    if not chain:
        return peer or UNKNOWN_CLIENT

    # With N trusted hops, the client is N entries from the end. A chain shorter than expected
    # means someone sent fewer entries than the deployment appends, so the leftmost is the most
    # conservative choice available.
    index = len(chain) - trusted_proxy_hops
    return chain[max(0, index)]
