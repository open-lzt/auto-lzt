"""EgressPolicy — the only thing standing between a third-party flow module and this host's
private network.

A module is data, not code (``modules/validator.py`` enforces that), but a module can still name a
URL for a request node to fetch. That makes every URL in a module attacker-controlled input, and
this process sits inside a compose network next to an unauthenticated-by-default Redis holding the
money-idempotency guards and the job queue. So the fence is not about tidy configuration: without
it, ``http://redis:6379`` in a community module is remote code execution in the worker.

Four rules, in order:

1. **Scheme.** ``https`` only. ``file:``, ``gopher:`` and friends never reach a resolver.
2. **Allow-list, exact, and host:PORT.** Default EMPTY — an unconfigured deployment reaches
   nothing. Matching is exact and never by suffix, because ``api.telegram.org.evil.com`` ends with
   an allowed name. The port is part of what is allowed: an entry means port 443 unless it is
   written ``host:port``. A free port meant an allow-listed name — pointed inward by its own
   operator's DNS, or a public host that also exposes something on 9200 — reached a service the
   allow-list never named.
3. **Resolve, then judge the address.** Never the hostname. ``2130706433``, ``0177.0.0.1``,
   ``127.1`` and ``::ffff:127.0.0.1`` are all loopback, and none of them looks like it as a string
   (R-20). ``getaddrinfo`` canonicalises them; ``ipaddress`` classifies what comes back. Any
   private answer in a multi-address reply blocks the whole name — a DNS reply mixing public and
   private addresses is an attack, not a deployment.
4. **Hand back the address that was checked.** ``ResolvedTarget.ip`` is what the caller must
   connect to. Re-resolving at connect time is the DNS-rebinding hole: the name that answered
   public during the check answers 127.0.0.1 a millisecond later (R-3).

Redirects are the fifth rule and live in the transport, which refuses them outright — a 302 is a
fresh URL chosen by the same untrusted source, and following it would re-open everything the first
four rules just closed (R-4).
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import urlsplit

from app.core.exceptions import AppError, ErrorCode

_ALLOWED_SCHEME = "https"
_DEFAULT_PORT = 443


class EgressBlockReason(StrEnum):
    HOST_NOT_ALLOWED = "host_not_allowed"
    PRIVATE_ADDRESS = "private_address"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link_local"  # 169.254.0.0/16 — cloud metadata
    PORT_NOT_ALLOWED = "port_not_allowed"
    REDIRECT_ATTEMPTED = "redirect_attempted"
    # Deviation from the frozen five: none of them describes `file:///etc/passwd` or a URL with no
    # host, and "host_not_allowed" would be a lie about why it was refused.
    SCHEME_NOT_ALLOWED = "scheme_not_allowed"
    UNRESOLVABLE = "unresolvable"


class EgressBlocked(AppError):
    """Carries args, not formatted text. ``ip`` is None when the block preceded resolution."""

    status_code = 403
    code = ErrorCode.EGRESS_BLOCKED

    def __init__(self, host: str, ip: str | None, reason: EgressBlockReason) -> None:
        super().__init__(f"egress to {host} ({ip}) blocked: {reason.value}")
        self.host = host
        self.ip = ip
        self.reason = reason

    @property
    def client_message(self) -> str:
        # Names the host and the rule, but never the resolved ip: an operator debugging their
        # allow-list needs the former, and the latter is a free internal-network probe.
        return f"Outbound request to '{self.host}' refused: {self.reason.value}"


@dataclass(slots=True, frozen=True)
class ResolvedTarget:
    url: str
    host: str
    ip: IPv4Address | IPv6Address  # connect to THIS, never re-resolve (R-3)


def _unwrap(ip: IPv4Address | IPv6Address) -> IPv4Address | IPv6Address:
    """The real address behind an IPv6 wrapper. ``::ffff:127.0.0.1`` is loopback, but
    ``IPv6Address.is_loopback`` says False — only ``::1`` is loopback *as IPv6*. Judging the
    wrapper instead of its payload is exactly how a v4-mapped bypass gets through."""
    if isinstance(ip, IPv6Address):
        for inner in (ip.ipv4_mapped, ip.sixtofour):
            if inner is not None:
                return inner
        if ip.teredo is not None:
            return ip.teredo[1]
    return ip


def _classify(ip: IPv4Address | IPv6Address) -> EgressBlockReason | None:
    """Why this address is off-limits, or None if it is a public address."""
    real = _unwrap(ip)
    if real.is_loopback:
        return EgressBlockReason.LOOPBACK
    if real.is_link_local:
        return EgressBlockReason.LINK_LOCAL
    if real.is_private or real.is_reserved or real.is_multicast or real.is_unspecified:
        return EgressBlockReason.PRIVATE_ADDRESS
    return None


def _split_entry(entry: str) -> tuple[str, int]:
    """One allow-list entry as (hostname, port): ``host``, ``host:port``, ``[v6]`` or ``[v6]:port``.

    Split with ``urlsplit`` rather than ``rpartition(":")``, which cannot see an IPv6 literal —
    ``::1`` came out as host ``::`` on port 1 (an entry opening a port the operator never named),
    and ``[2001:db8::1]`` kept its brackets, so it never equalled the unbracketed hostname
    ``urlsplit`` returns from a URL. A bare, unbracketed v6 literal has no unambiguous port, so it
    is taken whole at the default one; anything else unparsable stays whole too and therefore
    matches nothing, which is the fail-closed direction.
    """
    try:
        parts = urlsplit(f"//{entry}")
        host, port = parts.hostname, parts.port
    except ValueError:
        host, port = None, None
    if not host:
        return entry.strip("[]"), _DEFAULT_PORT
    return host, port or _DEFAULT_PORT


class EgressPolicy:
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        """``allowed_hosts`` entries are ``host`` (meaning port 443) or ``host:port``. The same
        host may be listed more than once to allow several ports. An IPv6 host is written
        bracketed (``[::1]``, ``[::1]:8443``); a bare ``::1`` is accepted at the default port."""
        allowed: dict[str, set[int]] = {}
        for raw in allowed_hosts:
            entry = raw.strip().lower()
            if not entry:
                continue
            host, port = _split_entry(entry)
            allowed.setdefault(host, set()).add(port)
        self._allowed: dict[str, frozenset[int]] = {
            host: frozenset(ports) for host, ports in allowed.items()
        }

    def check_host(self, url: str) -> str:
        """The hostname, if scheme and allow-list permit it. Split out from ``resolve_and_check``
        so the transport can re-check a redirect's Location without a second DNS round trip when
        the hop is refused outright."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != _ALLOWED_SCHEME or not host:
            raise EgressBlocked(host, None, EgressBlockReason.SCHEME_NOT_ALLOWED)
        ports = self._allowed.get(host)
        if ports is None:
            raise EgressBlocked(host, None, EgressBlockReason.HOST_NOT_ALLOWED)
        if (parts.port or _DEFAULT_PORT) not in ports:
            raise EgressBlocked(host, None, EgressBlockReason.PORT_NOT_ALLOWED)
        return host

    async def resolve_and_check(self, url: str) -> ResolvedTarget:
        """The address to connect to for ``url``. Raises ``EgressBlocked``."""
        host = self.check_host(url)
        port = urlsplit(url).port or _DEFAULT_PORT
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise EgressBlocked(host, None, EgressBlockReason.UNRESOLVABLE) from exc
        if not infos:
            raise EgressBlocked(host, None, EgressBlockReason.UNRESOLVABLE)

        resolved = [ip_address(info[4][0]) for info in infos]
        # Every answer must be clean, not just the one we would have used: a name answering
        # [1.2.3.4, 127.0.0.1] is an attacker hedging against which address we pick.
        for ip in resolved:
            reason = _classify(ip)
            if reason is not None:
                raise EgressBlocked(host, str(ip), reason)
        return ResolvedTarget(url=url, host=host, ip=resolved[0])
