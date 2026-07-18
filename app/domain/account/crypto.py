"""Per-tenant token encryption at rest.

Envelope-style: a per-tenant data key is derived (HKDF) from the master key + tenant_id, so a
single leaked ciphertext is scoped to one tenant, and the blob carries a key-version byte so the
master key can be rotated without a flag-day re-encrypt (F-7/F-10 in the review ledger).
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


class MasterKeyMissing(Exception):
    """Master key was not configured — refuse to encrypt/decrypt (fail loud)."""


def _derive_tenant_key(master_key: str, tenant_id: TenantId) -> bytes:
    if not master_key:
        raise MasterKeyMissing
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(tenant_id).encode(),
        info=b"lzt-flow.token-dek.v%d" % _KEY_VERSION,
    )
    dek = hkdf.derive(master_key.encode())
    return base64.urlsafe_b64encode(dek)


class EnvelopeCipher:
    """Encrypts/decrypts marketplace tokens per tenant. Constructed with the master key."""

    def __init__(self, master_key: str) -> None:
        self._master_key = master_key

    def encrypt(self, token: str, tenant_id: TenantId) -> bytes:
        fernet = Fernet(_derive_tenant_key(self._master_key, tenant_id))
        return bytes([_KEY_VERSION]) + fernet.encrypt(token.encode())

    def fingerprint_token(self, token: str) -> str:
        """Deterministic HMAC-SHA256 hex digest of the plaintext token — used ONLY to let the DB
        enforce "this tenant already has this token" via a unique index. Unlike ``encrypt`` (Fernet
        is randomized, never equal for the same input twice), this must be equal for equal tokens;
        it is not itself reversible and never substitutes for the ciphertext at rest."""
        if not self._master_key:
            raise MasterKeyMissing
        return hmac.new(self._master_key.encode(), token.encode(), hashlib.sha256).hexdigest()

    def decrypt(self, blob: bytes, tenant_id: TenantId) -> str:
        version = blob[0]
        if version != _KEY_VERSION:
            raise MasterKeyMissing(f"unknown key version {version}")
        fernet = Fernet(_derive_tenant_key(self._master_key, tenant_id))
        return fernet.decrypt(blob[1:]).decode()
