"""The run budget must count money SPENT, not lots looked at.

The gate began as `cap = budget // max_price` feeding `logic.take`, which capped the CANDIDATE
list. A lot the marketplace refuses moves no money but consumed a slot all the same, so a run with
budget 300 and max_price 100 gave up after three refusals with the budget untouched and affordable
lots still in the same result page. Refusal is not rare on cheap lots — it was the majority outcome
in both live passes — so the template systematically under-bought, and worse the cheaper the target.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.catalog.nodes.fast_buy import FastBuyNode
from app.domain.flow_engine.errors import RunFailed
from app.domain.purchases.model import Purchase, PurchaseId
from tests.fixtures.flow_fakes import (
    TENANT,
    FakeGuard,
    FakeMarket,
    FakePurchases,
    build_ctx,
    build_node,
)


def _buy_node(**inputs: object) -> object:
    return build_node("buy", "market.fast_buy", inputs)


def _spent(ledger: FakePurchases, run_id: UUID, amount: int) -> None:
    """Put money on the ledger for `run_id` — the only thing the gate reads."""
    ledger.recorded.append(
        Purchase(
            id=PurchaseId(uuid4()),
            tenant_id=TENANT,
            item_id=1,
            price=Decimal(amount),
            currency="rub",
            category_id=1,
            run_id=run_id,
            node_id="buy",
            purchased_at=datetime.now(UTC),
        )
    )


async def test_a_refused_lot_costs_nothing_and_the_next_one_is_still_bought() -> None:
    """The defect itself. Three refusals must not consume a budget that covers four lots."""
    market, ledger = FakeMarket(), FakePurchases()
    market.fast_buy_price = 100
    node = _buy_node(
        item_id=7, dry_run=False, max_price=100, max_price_currency="rub", run_budget=400
    )

    # Nothing was ever recorded — the refusals moved no money — so the fourth attempt must proceed.
    ctx = build_ctx(node, market, FakeGuard(), purchases=ledger)
    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is True
    assert result.output["budget_exhausted"] is False


async def test_the_budget_stops_the_run_once_the_money_is_actually_gone() -> None:
    """350 already paid, 100 more would be 450 against a 400 budget — refuse, and say why."""
    market, ledger = FakeMarket(), FakePurchases()
    market.fast_buy_price = 100
    node = _buy_node(
        item_id=7, dry_run=False, max_price=100, max_price_currency="rub", run_budget=400
    )
    ctx = build_ctx(node, market, FakeGuard(), purchases=ledger)
    _spent(ledger, ctx.run_id, 350)

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is False
    assert result.output["budget_exhausted"] is True
    # The loop reads this key through the template's stop_condition; a purchase must never leave it
    # set, or one bought lot would end the run.
    assert market.fast_buy_pooled_calls == [], "the marketplace was called despite no budget"


async def test_an_unset_budget_buys_exactly_as_before() -> None:
    """This node is used outside the autobuy template; an unwired port changes nothing."""
    market, ledger = FakeMarket(), FakePurchases()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, FakeGuard(), purchases=ledger)

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is True
    assert result.output["budget_exhausted"] is False


async def test_an_unreadable_ledger_refuses_the_next_purchase_rather_than_buying_blind() -> None:
    """Fail-closed, and it is not paranoia: the ledger write is best-effort by design.

    A failed write must never fail a purchase that already happened — which means the ledger CAN
    undercount. Authorising the next purchase on a total we cannot read would spend the budget on
    a number we know might be wrong. A run that stops early costs nothing.
    """
    market = FakeMarket()
    ledger = FakePurchases(fail_with=RuntimeError("postgres is down"))
    node = _buy_node(
        item_id=7, dry_run=False, max_price=100, max_price_currency="rub", run_budget=400
    )

    result = await FastBuyNode().execute(build_ctx(node, market, FakeGuard(), purchases=ledger))

    assert result.output["purchased"] is False
    assert result.output["budget_exhausted"] is True
    assert market.fast_buy_pooled_calls == []


async def test_a_budget_without_a_price_ceiling_is_refused_at_parse_time() -> None:
    """Without `max_price` the next lot has no known maximum cost, so the gate would be theatre."""
    market = FakeMarket()
    node = _buy_node(item_id=7, dry_run=False, run_budget=400)

    with pytest.raises(RunFailed, match="run_budget requires max_price"):
        await FastBuyNode().execute(build_ctx(node, market, FakeGuard(), purchases=FakePurchases()))


async def test_a_dry_run_never_consults_the_budget() -> None:
    """A rehearsal records nothing, so its spend is always 0 — the query would only cost a query."""
    market, ledger = FakeMarket(), FakePurchases()
    node = _buy_node(
        item_id=7, dry_run=True, max_price=100, max_price_currency="rub", run_budget=400
    )

    result = await FastBuyNode().execute(build_ctx(node, market, FakeGuard(), purchases=ledger))

    assert result.output["purchased"] is False
    assert result.output.get("budget_exhausted", False) is False
    # The count, not the output: asserting only on the output passes with the skip deleted, because
    # a dry run reports the same thing whether or not it asked the ledger anything.
    assert ledger.spend_reads == 0


async def test_a_spend_that_never_reached_the_ledger_stops_the_run() -> None:
    """The same outage must not fail closed on a read and open on a write.

    The write is best-effort, so a lost row does not fail the purchase — but the gate reads the
    ledger and nothing else, so that spend is now invisible and every later lot would be authorised
    against a total that stopped moving. Numbers from the review: budget 1000, ceiling 300, ledger
    down — the run would buy twenty lots for 6000 without raising anything.
    """
    market, ledger = FakeMarket(), FakePurchases()
    ledger.record_fails = True
    node = _buy_node(
        item_id=7, dry_run=False, max_price=300, max_price_currency="rub", run_budget=1000
    )

    result = await FastBuyNode().execute(build_ctx(node, market, FakeGuard(), purchases=ledger))

    assert result.output["purchased"] is True, "the purchase happened; it must not be undone"
    assert result.output["budget_exhausted"] is True


async def test_a_lost_ledger_row_without_a_budget_is_only_bookkeeping() -> None:
    """Nothing to overrun, so a broken ledger has no reason to stop the run."""
    market, ledger = FakeMarket(), FakePurchases()
    ledger.record_fails = True

    result = await FastBuyNode().execute(
        build_ctx(_buy_node(item_id=7, dry_run=False), market, FakeGuard(), purchases=ledger)
    )

    assert result.output["purchased"] is True
    assert result.output["budget_exhausted"] is False


async def test_the_budget_allows_a_purchase_that_lands_exactly_on_it() -> None:
    """`<=`, not `<`: spending the last rouble of the budget is inside it."""
    market, ledger = FakeMarket(), FakePurchases()
    ctx = build_ctx(
        _buy_node(item_id=7, dry_run=False, max_price=100, max_price_currency="rub", run_budget=100),
        market,
        FakeGuard(),
        purchases=ledger,
    )

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is True
