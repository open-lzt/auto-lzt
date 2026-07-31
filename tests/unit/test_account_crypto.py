"""EnvelopeCipher: blob handling and fingerprint scoping."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest

from app.domain.account.crypto import EnvelopeCipher, TokenBlobInvalid
from app.domain.account.model import TenantId

KEY = base64.urlsafe_b64encode(b"7" * 32).decode()


def _cipher() -> EnvelopeCipher:
    return EnvelopeCipher(master_key=KEY)


def test_roundtrip() -> None:
    tenant = TenantId(uuid4())
    cipher = _cipher()
    assert cipher.decrypt(cipher.encrypt("tok", tenant), tenant) == "tok"


@pytest.mark.parametrize("blob", [b"", b"\x01"])
def test_a_truncated_blob_is_a_domain_error_not_an_indexerror(blob: bytes) -> None:
    """``blob[0]`` on an empty column raised IndexError, which reads as a bug in the app rather
    than as unreadable stored data."""
    with pytest.raises(TokenBlobInvalid):
        _cipher().decrypt(blob, TenantId(uuid4()))


def test_an_unknown_key_version_is_refused() -> None:
    with pytest.raises(TokenBlobInvalid) as exc:
        _cipher().decrypt(b"\x09payload", TenantId(uuid4()))
    assert exc.value.version == 9


def test_the_same_token_fingerprints_differently_per_tenant() -> None:
    """The digest used to be HMAC(master_key, token) alone, so identical fingerprints in the column
    told anyone reading it that two tenants held the same credential."""
    cipher = _cipher()
    one, two = TenantId(uuid4()), TenantId(uuid4())
    assert cipher.fingerprint_token("tok", one) != cipher.fingerprint_token("tok", two)
    assert cipher.fingerprint_token("tok", one) == cipher.fingerprint_token("tok", one)
