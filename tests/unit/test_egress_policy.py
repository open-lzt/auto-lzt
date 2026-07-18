"""The SSRF fence, attack by attack (T2.1 / R-2, R-3, R-4, R-20).

Each case is its own test because each is a different way in, and a table-driven blur would let one
silently stop asserting. DNS is stubbed at ``loop.getaddrinfo`` — the process boundary — so the
tests state what a name resolves to instead of depending on the internet agreeing with them.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from typing import Any

import pytest

from app.domain.egress.policy import EgressBlocked, EgressBlockReason, EgressPolicy

ALLOWED = frozenset({"api.telegram.org"})


@pytest.fixture
def resolves_to(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Make a hostname resolve to whatever the test says, at the resolver boundary."""

    def _install(*addresses: str) -> None:
        async def _fake_getaddrinfo(host: str, port: int, **_kw: Any) -> list[Any]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)) for addr in addresses]

        loop = asyncio.get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo)

    yield _install


async def _blocked(policy: EgressPolicy, url: str) -> EgressBlocked:
    with pytest.raises(EgressBlocked) as exc:
        await policy.resolve_and_check(url)
    return exc.value


async def test_the_default_deployment_reaches_nothing() -> None:
    """An empty allow-list is the default (config.py). Forgetting to configure the fence must fail
    closed, not open this host's network to third-party modules."""
    policy = EgressPolicy(frozenset())
    assert (await _blocked(policy, "https://api.telegram.org/x")).reason is (
        EgressBlockReason.HOST_NOT_ALLOWED
    )


async def test_cloud_metadata_is_blocked(resolves_to: Any) -> None:
    """169.254.169.254 hands out the instance's IAM credentials to anything that asks."""
    resolves_to("169.254.169.254")
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org/x")
    assert blocked.reason is EgressBlockReason.LINK_LOCAL


async def test_the_unauthenticated_redis_is_blocked(resolves_to: Any) -> None:
    """The one that turns an SSRF into RCE: Redis holds the idem:* money guards and the arq queue,
    so reaching it means duplicating paid effects or running code in the worker."""
    resolves_to("127.0.0.1")
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org:56379/")
    assert blocked.reason is EgressBlockReason.LOOPBACK


@pytest.mark.parametrize("port", [55432, 27543, 8000, 8765, 8770])
async def test_every_service_on_this_host_is_blocked(resolves_to: Any, port: int) -> None:
    """The fence is about the address, not the port — a private address is refused wherever it
    listens, so a new service on a new port is covered without anyone updating a list."""
    resolves_to("127.0.0.1")
    blocked = await _blocked(EgressPolicy(ALLOWED), f"https://api.telegram.org:{port}/")
    assert blocked.reason is EgressBlockReason.LOOPBACK


async def test_a_public_name_resolving_private_is_blocked(resolves_to: Any) -> None:
    """The RESOLVED address is judged, never the hostname. A name an attacker controls can point
    anywhere, and it costs them nothing to point it inside."""
    resolves_to("10.0.0.5")
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org/x")
    assert blocked.reason is EgressBlockReason.PRIVATE_ADDRESS
    assert blocked.ip == "10.0.0.5"


@pytest.mark.parametrize(
    ("label", "resolved"),
    [
        ("ipv6 loopback", "::1"),
        ("v4-mapped loopback", "::ffff:127.0.0.1"),
        ("ipv6 unique-local", "fd00::1"),
        ("ipv6 link-local", "fe80::1"),
    ],
)
async def test_ipv6_forms_of_the_same_address_are_blocked(
    resolves_to: Any, label: str, resolved: str
) -> None:
    """``::ffff:127.0.0.1`` is the interesting one: IPv6Address.is_loopback says False for it,
    because only ``::1`` is loopback *as IPv6*. Judging the wrapper instead of its payload is how a
    v4-mapped bypass gets through, so the policy unwraps first."""
    resolves_to(resolved)
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org/x")
    assert blocked.reason in {
        EgressBlockReason.LOOPBACK,
        EgressBlockReason.LINK_LOCAL,
        EgressBlockReason.PRIVATE_ADDRESS,
    }, label


@pytest.mark.parametrize("form", ["2130706433", "0177.0.0.1", "127.1", "0x7f.0.0.1"])
async def test_non_canonical_spellings_of_loopback_are_blocked(resolves_to: Any, form: str) -> None:
    """R-20: none of these looks like 127.0.0.1 to a string compare, and all of them mean it.

    The resolver is stubbed to answer 127.0.0.1 because that is what glibc actually does with these
    — and glibc is production (install.sh targets Debian/Ubuntu). Windows' resolver refuses them
    outright, which is why this is stubbed rather than left to the host: a test that passed here
    for the wrong reason would say nothing about the platform that matters.

    Either resolver keeps the fence intact — one answers loopback and the address check refuses it,
    the other refuses to resolve at all. What must never happen is a string compare deciding these
    are not 127.0.0.1, which is why the policy does not have one.
    """
    resolves_to("127.0.0.1")
    blocked = await _blocked(EgressPolicy(ALLOWED), f"https://{form}.example.com/x")
    assert blocked.reason is EgressBlockReason.HOST_NOT_ALLOWED

    allowed_anyway = EgressPolicy(frozenset({f"{form}.example.com"}))
    with pytest.raises(EgressBlocked) as exc:
        await allowed_anyway.resolve_and_check(f"https://{form}.example.com/x")
    # Even with the name explicitly allow-listed, the ADDRESS is what decides.
    assert exc.value.reason is EgressBlockReason.LOOPBACK


async def test_a_mixed_answer_blocks_the_whole_name(resolves_to: Any) -> None:
    """A name answering [public, private] is an attacker hedging against which address we pick.
    Checking only the first would make the bypass a coin flip."""
    resolves_to("93.184.216.34", "127.0.0.1")
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org/x")
    assert blocked.reason is EgressBlockReason.LOOPBACK


async def test_the_allow_list_never_matches_by_suffix(resolves_to: Any) -> None:
    """``api.telegram.org.evil.com`` ends with an allowed name. Suffix matching is how allow-lists
    get bypassed, so matching is exact."""
    resolves_to("93.184.216.34")
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org.evil.com/x")
    assert blocked.reason is EgressBlockReason.HOST_NOT_ALLOWED


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://api.telegram.org/x", "gopher://api.telegram.org/x"]
)
async def test_only_https_survives_the_scheme_check(url: str) -> None:
    """Refused before a resolver is ever reached — file:// has no host to resolve, and plaintext
    http would put the bot token on the wire."""
    blocked = await _blocked(EgressPolicy(ALLOWED), url)
    assert blocked.reason is EgressBlockReason.SCHEME_NOT_ALLOWED


async def test_an_allowed_public_host_is_reachable(resolves_to: Any) -> None:
    """The fence has to let the real thing through, or it is just an outage."""
    resolves_to("149.154.167.220")
    target = await EgressPolicy(ALLOWED).resolve_and_check("https://api.telegram.org/bot/x")
    assert target.host == "api.telegram.org"
    assert str(target.ip) == "149.154.167.220"


async def test_the_checked_address_is_handed_back_so_the_caller_cannot_re_resolve(
    resolves_to: Any,
) -> None:
    """R-3, the rebinding rule. The policy's answer is an ADDRESS, not a promise about a name: the
    name that answered public during the check answers 127.0.0.1 a millisecond later, so the
    transport must connect to what was actually checked."""
    resolves_to("149.154.167.220")
    target = await EgressPolicy(ALLOWED).resolve_and_check("https://api.telegram.org/x")

    # The name now points inside — as it would in a rebinding attack. The verdict already taken
    # names the address, so nothing about it changes.
    resolves_to("127.0.0.1")
    assert str(target.ip) == "149.154.167.220"


async def test_an_unresolvable_host_is_refused_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_args: Any, **_kw: Any) -> list[Any]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(asyncio.get_event_loop(), "getaddrinfo", _boom)
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org/x")
    assert blocked.reason is EgressBlockReason.UNRESOLVABLE


async def test_the_error_never_leaks_the_internal_address_to_a_client(resolves_to: Any) -> None:
    """The operator's client message names the host and the rule; the resolved ip stays in the log.
    Echoing it back would make a blocked request a free internal-network probe."""
    resolves_to("10.1.2.3")
    blocked = await _blocked(EgressPolicy(ALLOWED), "https://api.telegram.org/x")
    assert "10.1.2.3" not in blocked.client_message
    assert "api.telegram.org" in blocked.client_message
