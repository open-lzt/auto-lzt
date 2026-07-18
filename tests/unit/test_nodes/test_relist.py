"""RelistNode: always pinned (a new lot must belong to someone) and never idempotent."""

from __future__ import annotations

import pytest

from app.domain.account.errors import NoAvailableAccount
from app.domain.catalog.nodes.relist import RelistNode
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_account, build_ctx, build_node


async def test_relist_publishes_under_pinned_account() -> None:
    account = build_account()
    node = build_node(
        "rl1",
        "market.relist",
        {"price": 10.0, "category_id": 5, "currency": "usd", "item_origin": "resale"},
        account_ref=account.id,
    )
    market, guard = FakeMarket(), FakeGuard()

    async def load_account(tenant_id: object, account_id: object) -> object:
        return account

    result = await RelistNode().execute(build_ctx(node, market, guard, load_account=load_account))
    assert market.relist_calls
    assert result.output["item_id"] == 999


async def test_relist_without_any_account_ref_fails() -> None:
    node = build_node(
        "rl1",
        "market.relist",
        {"price": 10.0, "category_id": 5, "currency": "usd", "item_origin": "resale"},
    )
    market, guard = FakeMarket(), FakeGuard()
    with pytest.raises(NoAvailableAccount):
        await RelistNode().execute(build_ctx(node, market, guard))
