"""RepriceNode — thin wrapper over ``MarketAdapter.edit`` / ``pylzt``'s ``managing_edit``.

Two pricing strategies (wave-04 spec): an absolute ``price``, or a percentage ``decay_pct`` applied
to an upstream-supplied ``current_price`` (e.g. from ``get-my-lots``). Exactly one must resolve.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field
from pylzt.types import Currency

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_MUTATE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed


class RepriceInput(BaseSchema):
    item_id: int = Field(title="Лот", json_schema_extra={"x-ui": {"widget": "lot_ref"}}, gt=0)
    currency: str = Field(title="Валюта", json_schema_extra={"x-ui": {"widget": "select"}})
    price: int | None = Field(
        title="Новая цена",
        description="Задайте либо цену, либо процент скидки.",
        json_schema_extra={"x-ui": {"widget": "number"}},
        default=None,
        gt=0,
    )
    decay_pct: float | None = Field(
        title="Скидка, %",
        json_schema_extra={"x-ui": {"widget": "number"}},
        default=None,
        gt=0,
        lt=100,
    )
    current_price: int | None = Field(
        title="Текущая цена",
        description="Нужна только для расчёта скидки.",
        json_schema_extra={"x-ui": {"widget": "number"}},
        default=None,
        gt=0,
    )


class RepriceOutput(BaseSchema):
    item_id: int
    price: int
    currency: str


def _target_price(ctx: RunContext) -> int:
    price = ctx.resolve_optional("price")
    if price is not None:
        if isinstance(price, bool) or not isinstance(price, int | float):
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"reprice 'price' must be numeric, got {price!r}"
            )
        return int(price)

    decay_pct = ctx.resolve_optional("decay_pct")
    current_price = ctx.resolve_optional("current_price")
    if decay_pct is None or current_price is None:
        raise RunFailed(
            ctx.run_id,
            ctx.node.id,
            "reprice needs either 'price' or both 'decay_pct' and 'current_price'",
        )
    if isinstance(decay_pct, bool) or isinstance(current_price, bool):
        raise RunFailed(ctx.run_id, ctx.node.id, "reprice decay inputs must be numeric")
    # Decimal, not float — a float decay drifts by a unit at the rounding boundary (money rule).
    factor = Decimal(1) - Decimal(str(decay_pct)) / Decimal(100)
    target = Decimal(str(current_price)) * factor
    return int(target.to_integral_value(rounding=ROUND_HALF_UP))


class RepriceNode(BaseNode):
    node_type = "market.reprice"
    category = NodeCategory.ACTION
    idempotent = True
    # reprice edits an existing lot's price and spends nothing, so it mutates without being MONEY.
    capabilities = MARKET_MUTATE
    input_schema = RepriceInput
    output_schema = RepriceOutput
    required_inputs = ("item_id", "currency")
    batchable = True

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        item_id_raw = ctx.resolve_input("item_id")
        if isinstance(item_id_raw, bool) or not isinstance(item_id_raw, int | float | str):
            raise RunFailed(ctx.run_id, ctx.node.id, f"item_id must be an int, got {item_id_raw!r}")
        item_id = int(item_id_raw)

        currency_raw = ctx.resolve_input("currency")
        if not isinstance(currency_raw, str):
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"currency must be a str, got {currency_raw!r}"
            )
        currency = Currency(currency_raw)

        price = _target_price(ctx)

        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            return StepResultDTO(
                node_id=ctx.node.id, output={"item_id": item_id, "deduplicated": True}
            )

        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is not None:
            account = await ctx.deps.load_account(ctx.tenant_id, account_ref)
            result = await ctx.deps.market.reprice(item_id, account, price=price, currency=currency)
        else:
            result = await ctx.deps.market.reprice_via_pool(
                ctx.tenant_id, item_id, price=price, currency=currency
            )

        return StepResultDTO(
            node_id=ctx.node.id,
            output={"item_id": result.item_id, "price": result.price, "currency": result.currency},
        )
