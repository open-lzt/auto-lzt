"""RelistNode: always pinned (a new lot must belong to someone) and never idempotent."""

from __future__ import annotations

import pytest

from app.domain.account.errors import NoAvailableAccount
from app.domain.catalog.nodes.relist import RelistNode
from app.domain.flow_engine.errors import RunFailed
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


async def test_the_published_price_arrives_as_an_exact_int() -> None:
    """This port used to be `float` — the one money value in the module that was not exact, on the
    call that charges for a listing. The marketplace prices lots in whole units and every price it
    hands back is an int, so that is what leaves here."""
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

    await RelistNode().execute(build_ctx(node, market, guard, load_account=load_account))

    sent_price = market.relist_calls[0][0]
    assert sent_price == 10
    assert isinstance(sent_price, int) and not isinstance(sent_price, bool)


@pytest.mark.parametrize("price", [10.5, "10.5"])
async def test_a_fractional_price_is_refused_rather_than_rounded(price: object) -> None:
    """Rounding money silently is how you publish a lot at a price nobody chose, and the
    marketplace would round it anyway — so the disagreement surfaces here, before it costs a lot."""
    account = build_account()
    node = build_node(
        "rl1",
        "market.relist",
        {"price": price, "category_id": 5, "currency": "usd", "item_origin": "resale"},
        account_ref=account.id,
    )
    market, guard = FakeMarket(), FakeGuard()

    async def load_account(tenant_id: object, account_id: object) -> object:
        return account

    with pytest.raises(RunFailed, match="whole number"):
        await RelistNode().execute(build_ctx(node, market, guard, load_account=load_account))

    assert market.relist_calls == []
