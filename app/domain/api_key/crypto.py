"""Fingerprint for an API key — deliberately NOT the account fingerprint.

TRAP, stated before the code because the two look interchangeable and are not:
``EnvelopeCipher.fingerprint_token(token, tenant_id)`` (``app/domain/account/crypto.py``) mixes the
tenant into the digest. It cannot be used here. Resolving a key is what PRODUCES the tenant, so a
tenant-scoped digest would need the answer as an input — the lookup would have to try every tenant
in the table to find the one whose digest matches, which is not a lookup.

The account digest was scoped for a real reason: two tenants may legitimately register the SAME
marketplace token, and an unscoped digest let anyone reading the column correlate them. That reason
does not transfer. An API key is minted here, from ``secrets.token_urlsafe`` — two tenants holding
the same key is not a case to support but a collision to prevent, and the unique index on the digest
is what prevents it.

So: same primitive (HMAC-SHA256 over the master key), different message, on purpose. Do not
"unify" these two functions — doing so breaks key lookup or re-opens the correlation leak,
depending on which direction it is unified.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.domain.account.crypto import MasterKeyMissing

# 32 bytes of entropy, urlsafe-encoded. Long enough that guessing is not a threat model.
_KEY_BYTES = 32


def mint_api_key() -> str:
    """A fresh raw key. Returned to the caller ONCE, at creation; only its digest is stored."""
    return secrets.token_urlsafe(_KEY_BYTES)


def fingerprint_api_key(master_key: str, raw_key: str) -> str:
    """Deterministic HMAC-SHA256 hex digest of a raw API key, tenant-independent by construction.

    Equal keys must produce equal digests — that equality IS the lookup. Raising on an empty master
    key rather than defaulting keeps a misconfigured process from writing digests that a correctly
    configured one would never match.
    """
    if not master_key:
        raise MasterKeyMissing
    return hmac.new(master_key.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
