"""Unit tests for TokenPool(flow). pylzt (pool/Client/Token), the repo and the cipher are mocked.

The load-bearing test is `test_excluded_account_survives_client_rebuild`: it proves the reversed
-source-of-truth bug (F-2) stays fixed — an account EXCLUDED in Postgres is re-quarantined on every
rebuild, so it never silently returns to rotation when the Client is rebuilt.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import app.domain.account.pool as pool_mod
from app.domain.account.errors import NoAvailableAccount
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _fake_sessionmaker() -> _FakeSession:
    return _FakeSession()


class _FakePool:
    def __init__(self, tokens: object, **kwargs: object) -> None:
        self.tokens = list(tokens)  # type: ignore[arg-type]
        self.quarantined: list[str] = []

    def quarantine(self, token_id: str) -> None:
        self.quarantined.append(token_id)

    async def aclose(self) -> None:
        return None


def _account(tenant_id: TenantId, status: AccountStatus) -> Account:
    return Account(
        id=AccountId(uuid4()),
        tenant_id=tenant_id,
        encrypted_token=b"ciphertext",
        created_at=datetime.now(UTC),
        status=status,
    )


@pytest.fixture
def patched_lztforge(monkeypatch: pytest.MonkeyPatch) -> list[_FakePool]:
    """Patch the pylzt constructors in the pool module; return the list of built pools."""
    created: list[_FakePool] = []

    def _make_pool(tokens: object, **kwargs: object) -> _FakePool:
        pool = _FakePool(tokens, **kwargs)
        created.append(pool)
        return pool

    monkeypatch.setattr(pool_mod, "RoundRobinTokenPool", _make_pool)
    monkeypatch.setattr(pool_mod, "Client", lambda **kwargs: MagicMock())
    monkeypatch.setattr(
        pool_mod, "Token", lambda token_id, credential: SimpleNamespace(token_id=token_id)
    )
    return created


def _patch_repo(monkeypatch: pytest.MonkeyPatch, accounts: list[Account]) -> MagicMock:
    repo = MagicMock()
    repo.list = AsyncMock(side_effect=lambda tenant_id: list(accounts))
    monkeypatch.setattr(pool_mod, "AccountRepository", MagicMock(return_value=repo))
    return repo


def _token_pool() -> TokenPool:
    cipher = MagicMock()
    cipher.decrypt = MagicMock(return_value="decrypted-token")
    return TokenPool(_fake_sessionmaker, cipher)  # type: ignore[arg-type]


async def test_no_active_accounts_raises(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.EXCLUDED)])
    pool = _token_pool()

    with pytest.raises(NoAvailableAccount):
        await pool.acquire(tenant_id)


async def test_excluded_account_is_quarantined_on_build(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    tenant_id = TenantId(uuid4())
    active = _account(tenant_id, AccountStatus.ACTIVE)
    excluded = _account(tenant_id, AccountStatus.EXCLUDED)
    _patch_repo(monkeypatch, [active, excluded])
    pool = _token_pool()

    await pool.acquire(tenant_id)

    built = patched_lztforge[-1]
    assert str(excluded.id) in built.quarantined
    assert str(active.id) not in built.quarantined


async def test_excluded_account_survives_client_rebuild(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    tenant_id = TenantId(uuid4())
    account_a = _account(tenant_id, AccountStatus.ACTIVE)
    accounts = [account_a]
    _patch_repo(monkeypatch, accounts)
    pool = _token_pool()

    await pool.acquire(tenant_id)
    first_pool = patched_lztforge[-1]
    assert first_pool.quarantined == []  # A is active on first build

    # A gets excluded in Postgres, a second account B is added → cache invalidated, Client rebuilt.
    accounts[0] = replace(account_a, status=AccountStatus.EXCLUDED)
    accounts.append(_account(tenant_id, AccountStatus.ACTIVE))
    await pool.invalidate(tenant_id)
    await pool.acquire(tenant_id)

    rebuilt = patched_lztforge[-1]
    assert rebuilt is not first_pool
    assert str(account_a.id) in rebuilt.quarantined  # EXCLUDED reapplied from Postgres, not lost


async def test_quarantine_account_syncs_cached_pool(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    tenant_id = TenantId(uuid4())
    active = _account(tenant_id, AccountStatus.ACTIVE)
    _patch_repo(monkeypatch, [active])
    pool = _token_pool()
    await pool.acquire(tenant_id)

    pool.quarantine_account(tenant_id, active.id)

    assert str(active.id) in patched_lztforge[-1].quarantined


async def test_market_base_url_reaches_pooled_client_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2 regression: LZT_FLOW_MARKET_BASE_URL must reach the pooled worker Client, redirecting
    BOTH the market and forum hosts — otherwise a testnet-mode run leaks to real prod-api hosts."""
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(pool_mod, "RoundRobinTokenPool", lambda tokens, **kw: _FakePool(tokens))
    monkeypatch.setattr(pool_mod, "Client", lambda **kwargs: captured.append(kwargs) or MagicMock())
    monkeypatch.setattr(
        pool_mod, "Token", lambda token_id, credential: SimpleNamespace(token_id=token_id)
    )
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.ACTIVE)])

    cipher = MagicMock()
    cipher.decrypt = MagicMock(return_value="decrypted-token")
    testnet = "http://127.0.0.1:8765"
    pool = TokenPool(_fake_sessionmaker, cipher, testnet)  # type: ignore[arg-type]
    await pool.acquire(tenant_id)

    assert captured, "Client was never constructed"
    config = captured[-1].get("config")
    assert config is not None, "pooled Client built without a ClientConfig — base_url override lost"
    assert config.base_url == testnet
    assert config.forum_base_url == testnet


async def test_the_pooled_client_does_not_carry_a_purchase_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a testnet override the pooled Client keeps the real prod hosts and no config at all.

    It used to be born with the 120s that ``fast-buy`` needs, because a shared Client could not be
    widened for one call. That made every pooled read wait 120s before giving up for the sake of one
    slow POST. The purchase now carries its own timeout on the request, so this asserts the read
    side got its default back — see ``test_purchase_timeout.py`` for the other half.
    """
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(pool_mod, "RoundRobinTokenPool", lambda tokens, **kw: _FakePool(tokens))
    monkeypatch.setattr(pool_mod, "Client", lambda **kwargs: captured.append(kwargs) or MagicMock())
    monkeypatch.setattr(
        pool_mod, "Token", lambda token_id, credential: SimpleNamespace(token_id=token_id)
    )
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.ACTIVE)])

    cipher = MagicMock()
    cipher.decrypt = MagicMock(return_value="decrypted-token")
    pool = TokenPool(_fake_sessionmaker, cipher)  # type: ignore[arg-type]
    await pool.acquire(tenant_id)

    assert captured[-1].get("config") is None, (
        "a pooled Client built with a config against prod hosts is the purchase ceiling coming back"
    )
