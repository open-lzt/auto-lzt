"""The only node in the catalog that spends money had no behavioural tests at all.

Nothing under ``tests/`` referenced ``fast_buy``, and ``FakeMarket`` did not even model it — so a
mutation replacing the strict ``dry_run`` coercion with a permissive one passed the entire suite.
Every case here is one that was silently unguarded.
"""

import pytest

from app.domain.catalog.nodes.fast_buy import FastBuyNode, _as_bool
from tests.fixtures.flow_fakes import (
    TENANT,
    FakeGuard,
    FakeMarket,
    build_account,
    build_ctx,
    build_node,
)


def _buy_node(**inputs: object) -> object:
    return build_node("buy", "market.fast_buy", inputs)


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "да", " TRUE "])
def test_a_recognised_true_is_a_dry_run(raw: str) -> None:
    assert _as_bool(raw, "dry_run") is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "нет"])
def test_a_recognised_false_is_a_real_purchase(raw: str) -> None:
    assert _as_bool(raw, "dry_run") is False


@pytest.mark.parametrize("raw", ["y", "1.0", "enabled", "", "maybe", "правда"])
def test_an_unrecognised_value_refuses_rather_than_buying(raw: str) -> None:
    """The regression this pins: these all used to coerce to False, and False spends real money.

    `_as_int` beside it has always raised on what it cannot read; this port guards a purchase and
    must be at least as strict.
    """
    with pytest.raises(ValueError, match="must be a bool"):
        _as_bool(raw, "dry_run")


async def test_a_dry_run_still_reports_the_lot_but_does_not_buy() -> None:
    market, guard = FakeMarket(), FakeGuard()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=True), market, guard)

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is False
    assert market.fast_buy_pooled_calls == [(TENANT, 7, True)]


async def test_dry_run_false_reaches_the_market_as_a_real_purchase() -> None:
    """If this ever passes with `True` in the tuple, the safety switch stopped being wired."""
    market, guard = FakeMarket(), FakeGuard()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard)

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is True
    assert market.fast_buy_pooled_calls == [(TENANT, 7, False)]


async def test_a_pinned_account_buys_under_that_account_and_never_through_the_pool() -> None:
    """Decision #18's shape for the money path: the operator's chosen account is the one charged."""
    account = build_account(TENANT)
    market, guard = FakeMarket(), FakeGuard()

    async def _load(tenant_id: object, account_id: object) -> object:
        return account

    ctx = build_ctx(
        _buy_node(item_id=7, dry_run=False),
        market,
        guard,
        active_account=account.id,
        load_account=_load,
    )
    await FastBuyNode().execute(ctx)

    assert market.fast_buy_pinned_calls == [(account.id, 7, False)]
    assert market.fast_buy_pooled_calls == []


async def test_a_second_run_on_the_same_key_does_not_buy_again() -> None:
    """A resumed run must not re-spend. The guard is consumed BEFORE the effect, so the second
    execute reports deduplicated and the market is never called a second time."""
    market, guard = FakeMarket(), FakeGuard()
    first_ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard)
    await FastBuyNode().execute(first_ctx)

    second_ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard)
    result = await FastBuyNode().execute(second_ctx)

    assert result.output["deduplicated"] is True
    assert result.output["purchased"] is False
    assert len(market.fast_buy_pooled_calls) == 1


async def test_a_lot_taken_by_someone_else_does_not_abort_the_run() -> None:
    """Cheap lots are contested, so this is the normal case for a sniper — aborting here meant the
    run died on its first candidate and never reached the second."""
    market, guard = FakeMarket(), FakeGuard()
    market.fast_buy_unavailable = "уже в очереди у другого покупателя"
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard)

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is False
    assert "очереди" in result.output["unavailable_reason"]
