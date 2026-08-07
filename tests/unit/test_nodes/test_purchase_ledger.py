"""The purchase ledger: what `fast_buy` records, and what it must never let the ledger break.

The ledger is written AFTER the money has left, which decides every case here. A row that fails to
appear is an accounting gap; a purchase reported as failed because a row failed to appear is a
second purchase on the engine's retry. So the write is best-effort by design, and these pin that
choice rather than leave it to whoever next reads the try/except.
"""

from decimal import Decimal

from app.domain.catalog.nodes.fast_buy import FastBuyNode
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


async def test_a_completed_purchase_is_recorded_with_what_was_actually_paid() -> None:
    market, guard, ledger = FakeMarket(), FakeGuard(), FakePurchases()
    market.fast_buy_price = 37
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard, purchases=ledger)

    await FastBuyNode().execute(ctx)

    assert len(ledger.recorded) == 1
    row = ledger.recorded[0]
    assert row.item_id == 7
    # Decimal, and the PAID price — the searched price is a different number in practice (a live
    # run found a telegram lot at 6 and paid 5).
    assert row.price == Decimal(37)
    assert row.currency == "rub"
    assert row.category_id == 100
    assert row.tenant_id == TENANT
    assert row.node_id == "buy"


async def test_a_dry_run_records_nothing() -> None:
    """A rehearsal that leaves a ledger row reads as real money spent."""
    market, guard, ledger = FakeMarket(), FakeGuard(), FakePurchases()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=True), market, guard, purchases=ledger)

    await FastBuyNode().execute(ctx)

    assert ledger.recorded == []


async def test_the_same_lot_is_never_recorded_twice() -> None:
    """A replayed step re-runs this write; `uq_purchases_tenant_item` is what refuses it."""
    market, guard, ledger = FakeMarket(), FakeGuard(), FakePurchases()
    node = _buy_node(item_id=7, dry_run=False)

    await FastBuyNode().execute(build_ctx(node, market, FakeGuard(), purchases=ledger))
    await FastBuyNode().execute(build_ctx(node, market, guard, purchases=ledger))

    assert len(ledger.recorded) == 1


async def test_a_broken_ledger_does_not_turn_a_completed_purchase_into_a_failure() -> None:
    """The one that matters most, and the one an "improvement" would remove.

    Letting this raise reports a bought lot as not-bought, and the engine's retry then buys a
    SECOND one. A missing row costs bookkeeping; a double purchase costs the lot's price.
    """
    market, guard = FakeMarket(), FakeGuard()
    ledger = FakePurchases(fail_with=RuntimeError("postgres is down"))
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, guard, purchases=ledger)

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is True
    assert result.output["item_id"] == 7
