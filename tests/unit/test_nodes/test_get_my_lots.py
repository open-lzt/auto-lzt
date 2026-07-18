"""GetMyLotsNode: manual page-loop over list_user (not Paginator.collect()), pinned account only."""

from __future__ import annotations

import json

import pytest

from app.domain.account.errors import NoAvailableAccount
from app.domain.catalog.nodes.get_my_lots import GetMyLotsNode
from app.domain.market.dtos import LotsPage
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_account, build_ctx, build_node


async def test_get_my_lots_pages_until_exhausted() -> None:
    account = build_account()
    market = FakeMarket()
    market.pages[(account.id, 1)] = LotsPage(item_ids=(1, 2), has_next_page=True)
    market.pages[(account.id, 2)] = LotsPage(item_ids=(3,), has_next_page=False)
    node = build_node("gml1", "logic.get_my_lots", {}, account_ref=account.id)

    async def load_account(tenant_id: object, account_id: object) -> object:
        return account

    result = await GetMyLotsNode().execute(
        build_ctx(node, market, FakeGuard(), load_account=load_account)
    )
    assert json.loads(result.output["item_ids"]) == [1, 2, 3]
    assert result.output["count"] == 3


async def test_get_my_lots_without_pinned_account_fails() -> None:
    node = build_node("gml1", "logic.get_my_lots", {})
    with pytest.raises(NoAvailableAccount):
        await GetMyLotsNode().execute(build_ctx(node, FakeMarket(), FakeGuard()))
