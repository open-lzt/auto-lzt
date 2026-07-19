"""Short-lived signed tokens that authorize one SSE subscription to one run.

``GET /runs/{id}/stream`` is consumed by the browser's ``EventSource``, which cannot set request
headers — so the ``X-API-Key`` gate that gates every other run read is physically unavailable on
that route. Rather than leave the stream open, the client trades its API key for a token at a
guarded POST endpoint and passes that token in the query string.

The token is bound to a single run id and expires in ``TOKEN_TTL_S``, so a leaked URL — query
strings end up in proxy logs, browser history, and Referer headers — is a minute-long window onto
one run the holder was already authorized to read, not a standing key to the whole API.

The signing key is derived from ``master_key`` rather than used directly: the same secret already
encrypts market tokens at rest, and a signing oracle must not share key material with a cipher.

The panel added a second subscription shape — one stream per tenant carrying task lifecycle, rather
than one per run — so a token now names a ``StreamScope`` alongside its subject. Scope separation is
done in the KEY DERIVATION, not in the signed payload, for two reasons. It keeps run-scope tokens
byte-identical to the ones this module minted before the panel existed, so nothing already issued
stops verifying and the existing tests hold unmodified. And it makes cross-scope confusion
cryptographically impossible rather than merely checked: a run token and a tenant token for the same
uuid are signed under different keys, so neither can ever validate as the other even though their
subject strings match exactly. A payload-level scope tag would be one forgotten comparison away from
letting a run token open the whole tenant feed.

BLAST RADIUS, stated plainly because it is larger than it looks: ``tenant_id_dep`` returns
``settings.default_tenant_id`` for every request (``app/core/tenant.py``) — this deployment is ONE
tenant per installation. A leaked tenant-scope token therefore exposes the whole installation's
task feed for ``TOKEN_TTL_S`` seconds, not one run's steps. The TTL is what bounds it; there is no
narrower subject to bind to until multi-tenancy is real.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from enum import StrEnum
from typing import Final


class StreamScope(StrEnum):
    """What a token authorizes. The value is part of the derived signing key, so adding a member
    creates a fresh key space rather than widening an existing one."""

    RUN = "run"
    TENANT = "tenant"


# One derivation info per scope. RUN's value is frozen: changing it invalidates every run token in
# flight and rewrites test vectors for no gain.
_DERIVE_INFO: Final[dict[StreamScope, bytes]] = {
    StreamScope.RUN: b"lzt-flow/sse-stream-token/v1",
    StreamScope.TENANT: b"lzt-flow/sse-tenant-stream-token/v1",
}
TOKEN_TTL_S: Final = 60
# The expiry as it must appear on the wire: ONE canonical decimal spelling per number. `int()` is
# far more generous — it accepts "+60", " 60", "0060", "6_0" and even non-ASCII digits, all of which
# parse to the same number and therefore carry the same valid MAC. That would give one authorization
# many byte-strings, so anything that ever keys off the token text (a revocation list, a replay
# cache, a log grep) could be walked straight past with a cosmetic variant.
#
# The leading zero is why this is a regex and not `expires_raw.isdigit()`: "0060" is all digits, and
# isdigit() is true for non-ASCII digits too. Both spellings would have sailed through.
_EXPIRES_RE: Final = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")


class StreamTokenInvalid(Exception):
    """Malformed, expired, or wrong-run token. One type for every failure on purpose: telling a
    caller *which* check failed tells an attacker which half of the token to keep guessing."""


class MasterKeyMissing(Exception):
    """No signing key is configured — the server is misconfigured, not the caller.

    Kept apart from StreamTokenInvalid deliberately. Folding it in there would report a deployment
    that cannot sign anything as "your token is bad": every operator would go hunting the client
    while the actual cause sat in the environment. It reaches the caller as a 500 — which is the
    truth — and no token verifies while it is raised, so refusing stays fail-closed either way.
    """


def _signing_key(master_key: str, scope: StreamScope) -> bytes:
    if not master_key:
        raise MasterKeyMissing()
    return hmac.new(master_key.encode(), _DERIVE_INFO[scope], hashlib.sha256).digest()


def _signature(master_key: str, subject: str, expires_at: int, scope: StreamScope) -> str:
    payload = f"{subject}:{expires_at}".encode()
    return hmac.new(_signing_key(master_key, scope), payload, hashlib.sha256).hexdigest()


def issue(
    master_key: str, subject: str, *, scope: StreamScope = StreamScope.RUN, now: int | None = None
) -> str:
    """A token authorizing ``subject``'s stream at ``scope`` for the next ``TOKEN_TTL_S`` seconds.

    ``scope`` defaults to RUN so every existing call site keeps its exact previous behaviour and
    output.
    """
    expires_at = (now if now is not None else int(time.time())) + TOKEN_TTL_S
    return f"{expires_at}.{_signature(master_key, subject, expires_at, scope)}"


def verify(
    master_key: str,
    subject: str,
    token: str,
    *,
    scope: StreamScope = StreamScope.RUN,
    now: int | None = None,
) -> None:
    """Raise ``StreamTokenInvalid`` unless ``token`` now authorizes ``subject`` at ``scope``."""
    expires_raw, _, signature = token.partition(".")
    if not signature or not _EXPIRES_RE.match(expires_raw):
        raise StreamTokenInvalid()
    expires_at = int(expires_raw)
    # Expiry is checked before the MAC so that an expired-but-valid token cannot be replayed, and
    # the MAC is still verified below so an attacker cannot forge a far-future expiry.
    if (now if now is not None else int(time.time())) >= expires_at:
        raise StreamTokenInvalid()
    if not hmac.compare_digest(signature, _signature(master_key, subject, expires_at, scope)):
        raise StreamTokenInvalid()
