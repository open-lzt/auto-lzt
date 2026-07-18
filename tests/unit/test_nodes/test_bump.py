"""BumpNode: pool path (no account_ref), pinned path (account_ref/active_account_id), and dedup."""

from __future__ import annotations

from app.domain.catalog.nodes.bump import BumpNode
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_account, build_ctx, build_node


async def test_bump_via_pool_when_no_account_ref() -> None:
    node = build_node("b1", "market.bump", {"item_id": 123})
    market, guard = FakeMarket(), FakeGuard()
    result = await BumpNode().execute(build_ctx(node, market, guard))
    assert market.bump_calls == [123]
    assert result.output["item_id"] == 123


async def test_bump_pinned_when_account_ref_set() -> None:
    account = build_account()
    node = build_node("b1", "market.bump", {"item_id": 123}, account_ref=account.id)
    market, guard = FakeMarket(), FakeGuard()

    async def load_account(tenant_id: object, account_id: object) -> object:
        assert account_id == account.id
        return account

    ctx = build_ctx(node, market, guard, load_account=load_account)
    await BumpNode().execute(ctx)
    assert market.bump_pinned_calls == [(account.id, 123)]
    assert market.bump_calls == []


async def test_bump_pinned_via_active_account_id_overrides_static_ref() -> None:
    """decision #18/#23: for_each_account's dynamic pin wins even though the node's own
    ``account_ref`` is unset."""
    account = build_account()
    node = build_node("b1", "market.bump", {"item_id": 123})
    market, guard = FakeMarket(), FakeGuard()

    async def load_account(tenant_id: object, account_id: object) -> object:
        return account

    ctx = build_ctx(node, market, guard, active_account=account.id, load_account=load_account)
    await BumpNode().execute(ctx)
    assert market.bump_pinned_calls == [(account.id, 123)]


async def test_bump_deduplicates_within_guard_window() -> None:
    node = build_node("b1", "market.bump", {"item_id": 123})
    market, guard = FakeMarket(), FakeGuard()
    ctx = build_ctx(node, market, guard)
    await BumpNode().execute(ctx)
    result = await BumpNode().execute(ctx)
    assert result.output["deduplicated"] is True
    assert market.bump_calls == [123]  # only the first call actually ran
