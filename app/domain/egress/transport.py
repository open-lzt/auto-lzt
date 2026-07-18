"""HttpTransport — the outbound-HTTP surface a node is *given*.

The policy is applied *here*, at the transport, not inside each node. A node that wants the network
goes through ``deps.http``, and ``deps.http`` cannot be constructed without an ``EgressPolicy``, so
there is no no-policy seam to reach for and nothing for a node author to remember.

**What that is worth, precisely.** This file used to claim the fence was "unbypassable" and that a
plugin author had "no second way to make a request". That is not true and it is worth stating
plainly, because a fence believed to be higher than it is gets trusted with things it cannot hold:

- Against a **module** (``kind: flow``) it does hold. A module is data — a graph of node types that
  already exist — so the only network it can reach is the network its nodes reach, and its nodes
  come through here. That is the case the fence is actually for: a module is reviewed by reading
  JSON, and the reader does not have to reason about what code it might run.
- Against a **plugin** (``kind: python``) it holds nothing. A plugin is code; ``ep.load()`` imports
  it, and ``import httpx`` is the second way. It could also replace this transport, or a node's
  ``execute``, without any registry collision.

Installing a plugin is an administrator trusting its author exactly as much as they trust this
engine, and that is defensible — ``pip install`` is not a thing strangers do to your box. What is
not defensible is thinking an isolation boundary exists here. See ``docs/plugins.md``.

Connecting to ``ResolvedTarget.ip`` rather than re-resolving the hostname is the anti-rebinding
rule (R-3), and it is why TLS needs help: the URL we hand httpx names an address, so certificate
verification and SNI are pointed back at the real hostname explicitly. Getting that wrong would
either break TLS or, worse, silently stop verifying it. It is also why ``build_transport`` turns
keep-alive off — see there.

Deviation from the frozen contract, which places ``HttpMethod``/``RequestSpec`` in
``catalog/nodes/base_request.py``: ``NodeDeps`` must name the transport's types, and every node
module already imports ``NodeDeps`` from ``base_node``. Defining them in the node module would make
``base_node`` import a node — a cycle. They live with the transport that consumes them, and
``base_request.py`` re-exports them for node authors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.domain.egress.policy import EgressBlocked, EgressBlockReason, EgressPolicy, ResolvedTarget

_MAX_RESPONSE_BYTES = 1 * 1024 * 1024


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass(slots=True, frozen=True)
class RequestSpec:
    url: str
    method: HttpMethod
    headers: Mapping[str, str]
    json_body: Mapping[str, Any] | None
    timeout_s: float


class HttpTransport(Protocol):
    """Implementations apply EgressPolicy before connecting — a node cannot reach the network any
    other way.

    The frozen contract spells this ``request(target: ResolvedTarget, spec: RequestSpec)``. Taking
    a pre-resolved target would mean the *caller* runs the policy and the transport trusts its
    verdict — so a node could resolve a host, get a clean answer, and hand over a hand-built
    ``ResolvedTarget`` naming 127.0.0.1. Resolution stays inside, where a node cannot forge it.
    """

    async def request(self, spec: RequestSpec) -> tuple[int, Mapping[str, Any]]: ...


class PolicedHttpTransport:
    """The production transport. Owns a policy and an httpx client; refuses anything the policy
    refuses, follows no redirects, and stops reading a body once it passes the cap."""

    def __init__(self, policy: EgressPolicy, client: httpx.AsyncClient) -> None:
        self._policy = policy
        self._client = client

    async def request(self, spec: RequestSpec) -> tuple[int, Mapping[str, Any]]:
        target = await self._policy.resolve_and_check(spec.url)
        response = await self._send(target, spec)
        try:
            # Redirects are never followed: a Location is a fresh URL chosen by the same untrusted
            # source, and following it re-opens everything the fence just closed (R-4). Re-checking
            # the hop through the policy would be defensible, but no node needs redirects, and
            # refusing is the smaller attack surface. Checked before the body is read, so a
            # redirect costs us its headers and nothing more.
            if response.is_redirect:
                raise EgressBlocked(
                    target.host, str(target.ip), EgressBlockReason.REDIRECT_ATTEMPTED
                )
            return response.status_code, await self._body(response)
        finally:
            # Streaming means the connection is ours until we say otherwise, including on the
            # raise above and on an over-limit body we walked away from mid-download.
            await response.aclose()

    async def _send(self, target: ResolvedTarget, spec: RequestSpec) -> httpx.Response:
        parts = urlsplit(target.url)
        # The literal IP the policy cleared, so the connection cannot land anywhere else. Brackets
        # for IPv6 — urlunsplit will not add them.
        ip = f"[{target.ip}]" if ":" in str(target.ip) else str(target.ip)
        netloc = f"{ip}:{parts.port}" if parts.port else ip
        pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        request = self._client.build_request(
            spec.method.value,
            pinned,
            headers={**dict(spec.headers), "Host": target.host},
            json=dict(spec.json_body) if spec.json_body is not None else None,
            timeout=spec.timeout_s,
            # Verifies the certificate against the real hostname and sends it as SNI, even though
            # the URL names an IP. Without this the request either fails verification or, if
            # someone "fixes" it by disabling verify, silently accepts any certificate.
            extensions={"sni_hostname": target.host},
        )
        # stream=True so headers land before the body does. That is what lets the redirect check
        # and the size cap below run BEFORE we have taken an unbounded amount of an endpoint's
        # output into this process.
        return await self._client.send(request, stream=True, follow_redirects=False)

    async def _body(self, response: httpx.Response) -> Mapping[str, Any]:
        """The response as a mapping, reading at most ``_MAX_RESPONSE_BYTES`` of it.

        A non-JSON or oversized body is data we report, not an error we raise: the node's
        ``parse_response`` decides what a bad body means for its own contract, and an alert node
        should not crash a run because an endpoint returned HTML.

        The cap is enforced *while* reading. It used to be `len(response.content) > MAX`, which
        reads the whole body first and then reports that it was too big — a bounded report of an
        unbounded read. An endpoint that answers with an endless stream would have taken the worker
        down with it, and "the endpoint is allow-listed" is not the same as "the endpoint is
        healthy": the fence's own premise is that what is on the other side may be hostile.
        """
        declared = response.headers.get("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) > _MAX_RESPONSE_BYTES:
            # It told us. Believe it and read nothing — a body still has to be refused below when
            # the header is absent, lies, or the response is chunked.
            return {"error": "response_too_large", "bytes": int(declared)}

        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > _MAX_RESPONSE_BYTES:
                # Walk away mid-download. The reported size is what we read before stopping, not
                # the true total — we deliberately no longer know it, which is the whole point.
                return {"error": "response_too_large", "bytes": size}
            chunks.append(chunk)

        raw = b"".join(chunks)
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"error": "not_json", "text": raw.decode("utf-8", errors="replace")[:2048]}
        return parsed if isinstance(parsed, dict) else {"data": parsed}


def build_transport(policy: EgressPolicy) -> PolicedHttpTransport:
    """The production transport: policed, no redirects, and no pooled connection reuse.

    Keep-alive is off because pinning the IP (R-3) collides with how httpx pools connections.
    httpcore keys reuse on ``request.url.origin`` alone — and our URL's origin names the IP, not
    the host. ``sni_hostname`` is read once, when the connection is established, and never enters
    the key (httpcore 1.0.9: `_async/connection.py` reads the extension in `_connect`;
    `_async/connection_pool.py` reuses on `can_handle_request(origin)`).

    So two allow-listed hosts sharing one IP — ordinary on a CDN — would share one connection, and
    a request for the second would travel over a TLS session that only ever proved it was the
    first. The second host's certificate would never be verified, which is exactly the guarantee
    the fence is supposed to be making. Both hosts being operator-trusted makes the harm small
    today; it stops being small the day an operator allow-lists a host it does not fully trust.

    The cost is a TLS handshake per request. That is the honest price here: this engine makes
    occasional API calls and already sleeps between retries, so a handshake is noise next to the
    round trip — and a correct fence is not the place to spend correctness on latency. A per-host
    client pool would keep both, but it would also mean the transport owning client lifecycles;
    revisit it if a flow ever makes enough calls for the handshake to show up in a trace.
    """
    return PolicedHttpTransport(
        policy,
        httpx.AsyncClient(
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        ),
    )
