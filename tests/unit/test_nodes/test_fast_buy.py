"""The only node in the catalog that spends money had no behavioural tests at all.

Nothing under ``tests/`` referenced ``fast_buy``, and ``FakeMarket`` did not even model it — so a
mutation replacing the strict ``dry_run`` coercion with a permissive one passed the entire suite.
Every case here is one that was silently unguarded.
"""

import pytest

from app.domain.catalog.nodes.fast_buy import FastBuyNode, _as_bool
from app.domain.flow_engine.errors import RunFailed
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
    execute never reaches the market.

    It used to answer `deduplicated: true, purchased: false` here; on the money path that claim is
    unsupportable, so the property this test owns is now just the one it can prove — the market is
    not called twice. What the replay REPORTS is pinned separately, below.
    """
    market, guard = FakeMarket(), FakeGuard()
    first_ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard)
    await FastBuyNode().execute(first_ctx)

    second_ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard)
    with pytest.raises(RunFailed):
        await FastBuyNode().execute(second_ctx)

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


async def test_a_replayed_real_purchase_refuses_to_report_it_as_not_bought() -> None:
    """The defect this pins: the dedup branch used to answer `purchased: false` on the money path.

    Reaching `execute` at all means the runtime found no COMPLETED RunStep — it returns a committed
    result before the node is ever constructed. So a consumed guard here means an attempt whose
    outcome nobody knows, and `fast_buy` has a named error for exactly that shape
    (`PurchaseOutcomeUnknown`: the marketplace may take the money after our client gives up).
    Telling the operator "not bought" was the one claim that cannot be supported.
    """
    market, guard = FakeMarket(), FakeGuard()
    await FastBuyNode().execute(build_ctx(_buy_node(item_id=7, dry_run=False), market, guard))

    with pytest.raises(RunFailed, match="outcome was lost"):
        await FastBuyNode().execute(build_ctx(_buy_node(item_id=7, dry_run=False), market, guard))

    assert len(market.fast_buy_pooled_calls) == 1, "the replay must not buy a second time"


async def test_a_replayed_dry_run_still_reports_deduplicated() -> None:
    """The quiet half of the same branch: no money can move under dry_run, so a replay there is
    genuinely 'nothing bought' and must stay a normal result rather than failing the run."""
    market, guard = FakeMarket(), FakeGuard()
    await FastBuyNode().execute(build_ctx(_buy_node(item_id=7, dry_run=True), market, guard))

    replay = build_ctx(_buy_node(item_id=7, dry_run=True), market, guard)
    result = await FastBuyNode().execute(replay)

    assert result.output["deduplicated"] is True
    assert result.output["purchased"] is False


async def test_a_price_ceiling_reaches_the_money_call() -> None:
    """`market.search`'s pmax filtered at SEARCH time; the seller can reprice before we pay. The
    node's job is to carry the ceiling to the adapter, which is where the comparison happens."""
    market, guard = FakeMarket(), FakeGuard()
    ctx = build_ctx(
        _buy_node(item_id=7, dry_run=False, max_price=500, max_price_currency="RUB"),
        market,
        guard,
    )

    await FastBuyNode().execute(ctx)

    assert market.fast_buy_ceilings == [500]
    assert market.fast_buy_ceiling_currencies == ["RUB"], "a ceiling without its unit is not one"


async def test_an_unwired_ceiling_keeps_the_old_unbounded_behaviour() -> None:
    """Optional on purpose: every flow authored before the port existed must keep working."""
    market, guard = FakeMarket(), FakeGuard()

    await FastBuyNode().execute(build_ctx(_buy_node(item_id=7, dry_run=False), market, guard))

    assert market.fast_buy_ceilings == [None]


@pytest.mark.parametrize("raw", [0, -1, "нет"])
async def test_an_unusable_ceiling_stops_the_run_instead_of_buying(raw: object) -> None:
    """A ceiling that silently read as 'no ceiling' would let the purchase through at any price —
    the exact failure this port exists to prevent, so it refuses like `dry_run` does.

    `RunFailed`, not a bare `ValueError`: `relist` already raised the typed error for exactly this
    class of defect, and the runtime wrapped the untyped one in a second `RunFailed` whose message
    the operator then had to read out of a nested repr."""
    market, guard = FakeMarket(), FakeGuard()
    ctx = build_ctx(
        _buy_node(item_id=7, dry_run=False, max_price=raw, max_price_currency="RUB"),
        market,
        guard,
    )

    with pytest.raises(RunFailed, match="max_price"):
        await FastBuyNode().execute(ctx)

    assert market.fast_buy_pooled_calls == []
