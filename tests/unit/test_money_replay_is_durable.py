"""The "refusing to buy again" promise must outlive Redis.

The only thing standing between a resumed run and a second purchase was `IdempotencyGuard`, whose
keys expire (default 3600s) and vanish entirely on a flush or on a restart of a Redis with no
persistence. After any of those the money branch did not fail — it bought again. The durable half
is the `RunStep` row in Postgres, which the runtime already writes BEFORE the node runs; the node
now reads it as `ctx.step_replay`.
"""

from __future__ import annotations

import pytest

from app.domain.catalog.nodes.fast_buy import FastBuyNode
from app.domain.flow_engine.errors import RunFailed
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node

pytestmark = pytest.mark.asyncio


def _buy_node(**inputs: object) -> object:
    return build_node("buy", "market.fast_buy", inputs)


async def test_an_expired_guard_key_does_not_reopen_the_purchase() -> None:
    """A fresh `FakeGuard` IS the state after a TTL expiry or a Redis flush: the key is gone. The
    step row is not, and the run is a resume of the same step — so this must still refuse."""
    market = FakeMarket()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, FakeGuard(), step_replay=True)

    with pytest.raises(RunFailed, match="outcome was lost"):
        await FastBuyNode().execute(ctx)

    assert market.fast_buy_pooled_calls == [], "a lost Redis key bought the lot a second time"


async def test_a_first_attempt_is_unaffected() -> None:
    """The durable check must not make every purchase impossible — a step nobody started before
    carries `step_replay=False` and buys exactly as it always did."""
    market = FakeMarket()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=False), market, FakeGuard())

    result = await FastBuyNode().execute(ctx)

    assert result.output["purchased"] is True


async def test_a_replayed_dry_run_is_still_just_a_replay() -> None:
    """No money can have moved under dry_run, so the durable signal must not turn a harmless
    resume into a failed run."""
    market = FakeMarket()
    ctx = build_ctx(_buy_node(item_id=7, dry_run=True), market, FakeGuard(), step_replay=True)

    result = await FastBuyNode().execute(ctx)

    assert result.output["deduplicated"] is True
    assert result.output["purchased"] is False
