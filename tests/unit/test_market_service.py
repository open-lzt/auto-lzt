"""Unit test for MarketService — the adapter is mocked, no network."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.model import Account, AccountId, TenantId
from app.domain.market.dtos import BumpResult
from app.domain.market.service import MarketService


def _account(cipher: EnvelopeCipher, tenant_id: TenantId) -> Account:
    return Account(
        id=AccountId(uuid4()),
        tenant_id=tenant_id,
        encrypted_token=cipher.encrypt("secret-token", tenant_id),
        created_at=datetime.now(UTC),
    )


async def test_bump_decrypts_and_delegates_to_adapter() -> None:
    key = base64.urlsafe_b64encode(b"0" * 32).decode()
    cipher = EnvelopeCipher(master_key=key)
    tenant_id = TenantId(uuid4())
    account = _account(cipher, tenant_id)
    service = MarketService(cipher=cipher)

    expected = BumpResult(item_id=42, bumped_at=datetime.now(UTC))
    with patch("app.domain.market.service.MarketAdapter") as adapter_cls:
        instance = adapter_cls.return_value
        instance.bump = AsyncMock(return_value=expected)
        result = await service.bump(42, account)

    # adapter built with the DECRYPTED token, never the ciphertext
    _, kwargs = adapter_cls.call_args
    assert kwargs["token"] == "secret-token"
    assert result == expected
