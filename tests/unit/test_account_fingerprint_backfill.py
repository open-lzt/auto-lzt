"""Dedup survives the tenant-scoping of ``fingerprint_token``.

The digest changed from ``HMAC(mk, token)`` to ``HMAC(mk, tenant + NUL + token)``, and revision 0014
nulls every digest written under the old scheme because a migration holds neither the master key nor
a plaintext token. Nothing else can refill them, so ``AccountService`` does it from the stored
ciphertext before the tenant's next dedup decision. Without that step the unique index sees a NULL
where the duplicate's digest should be — and NULLs never collide, so adding the same credential
twice quietly succeeds.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.model import Account, AccountId, TenantId
from app.domain.account.service import AccountService

KEY = base64.urlsafe_b64encode(b"7" * 32).decode()
TOKEN = "the-same-credential"


def _legacy_row(cipher: EnvelopeCipher, tenant: TenantId) -> Account:
    """A row as revision 0014 leaves it: readable ciphertext, no fingerprint."""
    return Account(
        id=AccountId(uuid4()),
        tenant_id=tenant,
        encrypted_token=cipher.encrypt(TOKEN, tenant),
        created_at=datetime.now(UTC),
        token_hash=None,
    )


async def test_a_nulled_row_gets_its_fingerprint_back_before_the_next_dedup_decision() -> None:
    cipher = EnvelopeCipher(master_key=KEY)
    tenant = TenantId(uuid4())
    row = _legacy_row(cipher, tenant)
    repo = MagicMock()
    repo.list = AsyncMock(return_value=[row])
    repo.set_token_hash = AsyncMock()
    service = AccountService(repo, cipher, MagicMock())

    await service._backfill_token_hashes(tenant)

    repo.set_token_hash.assert_awaited_once_with(
        tenant, row.id, cipher.fingerprint_token(TOKEN, tenant)
    )


async def test_an_undecryptable_row_is_skipped_not_fatal() -> None:
    """A row written under a rotated-away key version is undedupable either way; refusing to touch
    the tenant's accounts because one old row is unreadable would be the wrong trade."""
    cipher = EnvelopeCipher(master_key=KEY)
    tenant = TenantId(uuid4())
    unreadable = Account(
        id=AccountId(uuid4()),
        tenant_id=tenant,
        encrypted_token=b"\x09garbage",
        created_at=datetime.now(UTC),
        token_hash=None,
    )
    readable = _legacy_row(cipher, tenant)
    repo = MagicMock()
    repo.list = AsyncMock(return_value=[unreadable, readable])
    repo.set_token_hash = AsyncMock()

    await AccountService(repo, cipher, MagicMock())._backfill_token_hashes(tenant)

    assert repo.set_token_hash.await_count == 1
    assert repo.set_token_hash.await_args.args[1] == readable.id


async def test_a_row_that_already_has_a_fingerprint_is_left_alone() -> None:
    cipher = EnvelopeCipher(master_key=KEY)
    tenant = TenantId(uuid4())
    row = Account(
        id=AccountId(uuid4()),
        tenant_id=tenant,
        encrypted_token=cipher.encrypt(TOKEN, tenant),
        created_at=datetime.now(UTC),
        token_hash=cipher.fingerprint_token(TOKEN, tenant),
    )
    repo = MagicMock()
    repo.list = AsyncMock(return_value=[row])
    repo.set_token_hash = AsyncMock()

    await AccountService(repo, cipher, MagicMock())._backfill_token_hashes(tenant)

    repo.set_token_hash.assert_not_awaited()
