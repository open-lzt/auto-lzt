"""Per-tenant token encryption at rest.

Envelope-style: a per-tenant data key is derived (HKDF) from the master key + tenant_id, so a
single leaked ciphertext is scoped to one tenant, and the blob carries a key-version byte so the
master key can be rotated without a flag-day re-encrypt (F-7/F-10 in the review ledger).

The version byte sits OUTSIDE the Fernet token, so it is not covered by Fernet's own MAC. It is
not trusted as data either: the version is mixed into the HKDF ``info``, so a flipped byte derives
a different key and the Fernet MAC then fails. Tampering with it cannot select a weaker path, only
a failing one.

The master key itself is validated for shape where it enters the process (``Settings.master_key``,
base64-urlsafe 32 bytes). HKDF derives, it does not stretch — a passphrase here would leave the
whole table open to an offline dictionary attack, so this module must never be handed one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.domain.account.model import TenantId

_KEY_VERSION = 1  # bump on master-key rotation; old blobs still decrypt by version prefix
_MIN_BLOB_LEN = 2  # version byte + at least one byte of Fernet token


class MasterKeyMissing(Exception):
    """Master key was not configured — refuse to encrypt/decrypt (fail loud)."""


class TokenBlobInvalid(Exception):
    """The stored ciphertext is not a blob this cipher can read: truncated, or written under a key
    version this build does not know. Carries args, never pre-formatted text."""

    def __init__(self, *, length: int, version: int | None = None) -> None:
        super().__init__(f"unreadable token blob (length={length}, version={version})")
        self.length = length
        self.version = version


def _derive_tenant_key(master_key: str, tenant_id: TenantId, version: int) -> bytes:
    if not master_key:
        raise MasterKeyMissing
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(tenant_id).encode(),
        info=b"lzt-flow.token-dek.v%d" % version,
    )
    dek = hkdf.derive(master_key.encode())
    return base64.urlsafe_b64encode(dek)


class EnvelopeCipher:
    """Encrypts/decrypts marketplace tokens per tenant. Constructed with the master key."""

    def __init__(self, master_key: str) -> None:
        self._master_key = master_key

    def encrypt(self, token: str, tenant_id: TenantId) -> bytes:
        fernet = Fernet(_derive_tenant_key(self._master_key, tenant_id, _KEY_VERSION))
        return bytes([_KEY_VERSION]) + fernet.encrypt(token.encode())

    def fingerprint_token(self, token: str, tenant_id: TenantId) -> str:
        """Deterministic HMAC-SHA256 hex digest of the plaintext token, scoped to one tenant — used
        ONLY to let the DB enforce "this tenant already has this token" via a unique index.

        Scoped, because the digest used to be HMAC(master_key, token) alone: two tenants adding the
        same credential produced the same 64 hex chars, and anyone who could read the column learned
        that fact about accounts they had no business correlating. The tenant is mixed in with a
        NUL separator so a tenant id ending in the token's first bytes cannot alias another pair.

        Unlike ``encrypt`` (Fernet is randomized, never equal for the same input twice), this must
        be equal for equal tokens; it is not itself reversible and never substitutes for the
        ciphertext at rest.
        """
        if not self._master_key:
            raise MasterKeyMissing
        message = str(tenant_id).encode() + b"\x00" + token.encode()
        return hmac.new(self._master_key.encode(), message, hashlib.sha256).hexdigest()

    def decrypt(self, blob: bytes, tenant_id: TenantId) -> str:
        if len(blob) < _MIN_BLOB_LEN:
            # Reading blob[0] first turned an empty/truncated column into IndexError, which the
            # error handlers see as a bug rather than as bad stored data.
            raise TokenBlobInvalid(length=len(blob))
        version = blob[0]
        if version != _KEY_VERSION:
            raise TokenBlobInvalid(length=len(blob), version=version)
        fernet = Fernet(_derive_tenant_key(self._master_key, tenant_id, version))
        return fernet.decrypt(blob[1:]).decode()
