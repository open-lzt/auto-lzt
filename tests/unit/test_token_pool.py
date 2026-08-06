"""Unit tests for TokenPool(flow). pylzt (pool/Client/Token), the repo and the cipher are mocked.

The load-bearing test is `test_excluded_account_survives_client_rebuild`: it proves the reversed
-source-of-truth bug (F-2) stays fixed — an account EXCLUDED in Postgres is re-quarantined on every
rebuild, so it never silently returns to rotation when the Client is rebuilt.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pylzt import ClientConfig

import app.domain.account.pool as pool_mod
from app.domain.account.errors import NoAvailableAccount
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool
from app.domain.market.adapter import PURCHASE_TIMEOUT_S


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
        self.closed = False

    def quarantine(self, token_id: str) -> None:
        self.quarantined.append(token_id)

    async def aclose(self) -> None:
        self.closed = True


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
    monkeypatch.setattr(pool_mod, "Client", lambda **kwargs: _fake_client())
    monkeypatch.setattr(
        pool_mod, "Token", lambda token_id, credential: SimpleNamespace(token_id=token_id)
    )
    return created


def _patch_repo(monkeypatch: pytest.MonkeyPatch, accounts: list[Account]) -> MagicMock:
    repo = MagicMock()
    repo.list = AsyncMock(side_effect=lambda tenant_id: list(accounts))
    monkeypatch.setattr(pool_mod, "AccountRepository", MagicMock(return_value=repo))
    return repo


def _fake_client() -> MagicMock:
    """A stand-in Client whose ``aclose`` can be awaited — `_TenantPool.aclose` closes both."""
    return MagicMock(aclose=AsyncMock())


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
        async with pool.lease(tenant_id):
            pass


async def test_excluded_account_is_quarantined_on_build(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    tenant_id = TenantId(uuid4())
    active = _account(tenant_id, AccountStatus.ACTIVE)
    excluded = _account(tenant_id, AccountStatus.EXCLUDED)
    _patch_repo(monkeypatch, [active, excluded])
    pool = _token_pool()

    async with pool.lease(tenant_id):
        pass

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

    async with pool.lease(tenant_id):
        pass
    first_pool = patched_lztforge[-1]
    assert first_pool.quarantined == []  # A is active on first build

    # A gets excluded in Postgres, a second account B is added → cache invalidated, Client rebuilt.
    accounts[0] = replace(account_a, status=AccountStatus.EXCLUDED)
    accounts.append(_account(tenant_id, AccountStatus.ACTIVE))
    await pool.invalidate(tenant_id)
    async with pool.lease(tenant_id):
        pass

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
    async with pool.lease(tenant_id):
        pass

    pool.quarantine_account(tenant_id, active.id)

    assert str(active.id) in patched_lztforge[-1].quarantined


async def test_market_base_url_reaches_pooled_client_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2 regression: LZT_FLOW_MARKET_BASE_URL must reach the pooled worker Client, redirecting
    BOTH the market and forum hosts — otherwise a testnet-mode run leaks to real prod-api hosts."""
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(pool_mod, "RoundRobinTokenPool", lambda tokens, **kw: _FakePool(tokens))
    monkeypatch.setattr(
        pool_mod, "Client", lambda **kwargs: captured.append(kwargs) or _fake_client()
    )
    monkeypatch.setattr(
        pool_mod, "Token", lambda token_id, credential: SimpleNamespace(token_id=token_id)
    )
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.ACTIVE)])

    cipher = MagicMock()
    cipher.decrypt = MagicMock(return_value="decrypted-token")
    testnet = "http://127.0.0.1:8765"
    pool = TokenPool(_fake_sessionmaker, cipher, testnet)  # type: ignore[arg-type]
    async with pool.lease(tenant_id):
        pass

    assert captured, "Client was never constructed"
    config = captured[-1].get("config")
    assert config is not None, "pooled Client built without a ClientConfig — base_url override lost"
    assert config.base_url == testnet
    assert config.forum_base_url == testnet


async def test_only_the_purchase_client_carries_the_purchase_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two Clients over ONE token pool: the read one stock, the purchase one at 120s.

    A single shared Client at 120s (what this used to be) made every pooled read wait two minutes
    before giving up for the sake of one slow POST; a single shared Client at the stock 30s bought
    on a timeout shorter than ``fast-buy`` itself. Both halves are asserted here because fixing
    either one alone is what produced the other.

    The pool object must be the SAME instance: rate budget, quarantine and proxy stickiness live
    there, so a second pool would silently give the tenant a second rate allowance.
    """
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(pool_mod, "RoundRobinTokenPool", lambda tokens, **kw: _FakePool(tokens))
    monkeypatch.setattr(
        pool_mod, "Client", lambda **kwargs: captured.append(kwargs) or _fake_client()
    )
    monkeypatch.setattr(
        pool_mod, "Token", lambda token_id, credential: SimpleNamespace(token_id=token_id)
    )
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.ACTIVE)])

    cipher = MagicMock()
    cipher.decrypt = MagicMock(return_value="decrypted-token")
    pool = TokenPool(_fake_sessionmaker, cipher)  # type: ignore[arg-type]
    async with pool.lease(tenant_id):
        pass

    assert len(captured) == 2, "expected exactly a read Client and a purchase Client"
    read, purchase = captured
    stock = ClientConfig().request_timeout
    assert read["config"].request_timeout == stock, (  # type: ignore[union-attr]
        "the pooled READ client carries the purchase ceiling — every read now waits 120s"
    )
    assert purchase["config"].request_timeout == PURCHASE_TIMEOUT_S, (  # type: ignore[union-attr]
        "the purchase client lost its ceiling — fast-buy is back on a timeout shorter than itself"
    )
    assert read["token_pool"] is purchase["token_pool"], (
        "two token pools means two rate budgets and two quarantine sets for one tenant"
    )


async def test_invalidate_does_not_close_a_pool_a_lease_still_holds(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    """The use-after-close: adding or excluding an account mid-run used to aclose() the very Client
    the running task was already holding."""
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.ACTIVE)])
    pool = _token_pool()

    async with pool.lease(tenant_id):
        built = patched_lztforge[-1]
        await pool.invalidate(tenant_id)
        assert not built.closed, "closed underneath a live lease"

    assert built.closed, "the last lease to leave must close the retired pool"


async def test_invalidate_racing_a_build_must_not_close_the_pool_the_lease_receives(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    """The lease counter has to be incremented under the SAME lock hold that publishes the entry.

    ``_get_or_build`` used to publish the entry, release the tenant lock, and only then take it a
    second time to count the lease. An ``invalidate`` queued on that lock runs in the gap, sees
    ``leases == 0``, retires the entry and ``aclose()``s it — and the lease it was racing then hands
    its caller an already-closed Client. That is precisely the invariant the reference count exists
    to hold, and no test that leases and invalidates in sequence can reach it.
    """
    tenant_id = TenantId(uuid4())
    accounts = [_account(tenant_id, AccountStatus.ACTIVE)]
    building = asyncio.Event()
    finish_build = asyncio.Event()

    async def _list(_tenant_id: TenantId) -> list[Account]:
        building.set()
        await finish_build.wait()
        return list(accounts)

    repo = MagicMock()
    repo.list = _list
    monkeypatch.setattr(pool_mod, "AccountRepository", MagicMock(return_value=repo))
    pool = _token_pool()

    closed_at_lease: list[bool] = []

    async def _lease() -> None:
        async with pool.lease(tenant_id):
            closed_at_lease.append(patched_lztforge[-1].closed)

    leasing = asyncio.create_task(_lease())
    await building.wait()
    # Queued on the tenant lock the build is holding: it is woken the instant the build releases.
    invalidating = asyncio.create_task(pool.invalidate(tenant_id))
    await asyncio.sleep(0)
    finish_build.set()
    await asyncio.gather(leasing, invalidating)

    assert closed_at_lease == [False], "the lease was handed a Client invalidate had already closed"


async def test_invalidate_closes_immediately_when_nobody_holds_it(
    monkeypatch: pytest.MonkeyPatch, patched_lztforge: list[_FakePool]
) -> None:
    tenant_id = TenantId(uuid4())
    _patch_repo(monkeypatch, [_account(tenant_id, AccountStatus.ACTIVE)])
    pool = _token_pool()

    async with pool.lease(tenant_id):
        pass
    built = patched_lztforge[-1]
    await pool.invalidate(tenant_id)

    assert built.closed
