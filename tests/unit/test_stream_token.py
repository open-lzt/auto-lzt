"""The SSE stream token, at the unit level.

The route tests cover the happy path and the tenant check; these cover the token as a piece of
crypto — the cases a route test cannot reach because it can only ask for well-formed tokens.
"""

from __future__ import annotations

import pytest

from app.core.stream_token import (
    TOKEN_TTL_S,
    MasterKeyMissing,
    StreamTokenInvalid,
    issue,
    verify,
)

KEY = "a-master-key"
RUN = "8f14e45f-ceea-467a-9a3b-000000000001"
OTHER_RUN = "8f14e45f-ceea-467a-9a3b-000000000002"
NOW = 1_000_000


def test_a_fresh_token_verifies() -> None:
    verify(KEY, RUN, issue(KEY, RUN, now=NOW), now=NOW + 1)


def test_a_token_for_one_run_does_not_open_another() -> None:
    """The run id is inside the MAC, not just alongside it. Otherwise a token for a run you own
    would stream a run you do not — and getting a token for your own run is trivial."""
    token = issue(KEY, RUN, now=NOW)

    with pytest.raises(StreamTokenInvalid):
        verify(KEY, OTHER_RUN, token, now=NOW + 1)


def test_a_token_expires() -> None:
    token = issue(KEY, RUN, now=NOW)

    verify(KEY, RUN, token, now=NOW + TOKEN_TTL_S - 1)
    with pytest.raises(StreamTokenInvalid):
        verify(KEY, RUN, token, now=NOW + TOKEN_TTL_S)


def test_a_token_signed_with_another_key_is_refused() -> None:
    token = issue("someone-elses-key", RUN, now=NOW)

    with pytest.raises(StreamTokenInvalid):
        verify(KEY, RUN, token, now=NOW + 1)


def test_a_forged_far_future_expiry_is_refused() -> None:
    """Expiry is checked before the MAC, so this is the case that ordering could have opened: an
    attacker who could stretch the expiry without the key would hold a permanent token."""
    forever = f"{NOW + 10**6}.{issue(KEY, RUN, now=NOW).partition('.')[2]}"

    with pytest.raises(StreamTokenInvalid):
        verify(KEY, RUN, forever, now=NOW + 1)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda e: f"+{e}", id="plus-sign"),
        pytest.param(lambda e: f" {e}", id="leading-space"),
        pytest.param(lambda e: f"0{e}", id="leading-zero"),
        pytest.param(lambda e: f"{str(e)[:-1]}_{str(e)[-1]}", id="underscore-separator"),
        pytest.param(
            lambda e: str(e).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")),
            id="arabic-indic-digits",
        ),
    ],
)
def test_a_non_canonical_expiry_is_refused(mutate: object) -> None:
    """``int()`` accepts every one of these and returns the SAME number, so each would carry a
    valid MAC and verify. One authorization with many byte-strings defeats anything that keys off
    the token text — a revocation list, a replay cache, a log grep.

    Not a privilege escalation today: nothing keys off the text yet. It is refused here so that the
    day something does, it is not silently already bypassable.
    """
    expires_at, _, signature = issue(KEY, RUN, now=NOW).partition(".")

    with pytest.raises(StreamTokenInvalid):
        verify(KEY, RUN, f"{mutate(int(expires_at))}.{signature}", now=NOW + 1)  # type: ignore[operator]


@pytest.mark.parametrize(
    "token",
    ["", ".", "abc", "nodot", f"{NOW + 60}.", f".{'0' * 64}", "999999999999999999999999.aa"],
)
def test_a_malformed_token_is_refused(token: str) -> None:
    with pytest.raises(StreamTokenInvalid):
        verify(KEY, RUN, token, now=NOW)


def test_an_unconfigured_server_says_so_instead_of_blaming_the_token() -> None:
    """A deployment with no master key cannot sign anything. Reporting that as StreamTokenInvalid
    would send every operator hunting the client while the cause sat in the environment.

    Still fail-closed: no token verifies while this is raised — it is a 500, not a way in.
    """
    with pytest.raises(MasterKeyMissing):
        issue("", RUN, now=NOW)

    with pytest.raises(MasterKeyMissing):
        verify("", RUN, f"{NOW + 60}.{'0' * 64}", now=NOW)


def test_an_expired_token_is_refused_even_before_the_key_is_consulted() -> None:
    """Expiry precedes the MAC compare, which also means an expired token is refused on a server
    with no key — the ordering must not turn a misconfiguration into an acceptance."""
    with pytest.raises((StreamTokenInvalid, MasterKeyMissing)):
        verify("", RUN, f"{NOW - 1}.{'0' * 64}", now=NOW)
