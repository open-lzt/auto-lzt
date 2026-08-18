"""RepriceNode — thin wrapper over ``MarketAdapter.edit`` / ``pylzt``'s ``managing_edit``.

**ON HOLD — do not put this node into presets, docs or onboarding flows. Owner's call,
2026-08-18.** The node stays in the catalog because existing flows reference it and removing it
would break them; what is frozen is its SPREAD, not its existence. Only the owner lifts this.

Concretely, until then: no new preset wires it, no guide teaches it, no example uses it. A flow
that already has it keeps working.

Two pricing strategies (wave-04 spec): an absolute ``price``, or a percentage ``decay_pct`` applied
to an upstream-supplied ``current_price`` (e.g. from ``get-my-lots``). Exactly one must resolve.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field
from pylzt.types import Currency

from app.core.schema import BaseSchema, FractionalPort, NumericPort
from app.domain.catalog.capabilities import MARKET_MUTATE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed


class RepriceInput(BaseSchema):
    item_id: NumericPort = Field(
        title="Лот", json_schema_extra={"x-ui": {"widget": "lot_ref"}}, gt=0
    )
    # `Currency`, а не `str`: тело узла всё равно звало `Currency(...)`, поэтому неизвестная
    # валюта проходила схему и падала ГОЛЫМ `ValueError` — рантайм заворачивал его вторым
    # `RunFailed`, и оператор читал причину из вложенного repr. Теперь отказ приходит от схемы и
    # называет допустимые значения.
    currency: Currency = Field(title="Валюта", json_schema_extra={"x-ui": {"widget": "select"}})
    price: NumericPort | None = Field(
        title="Новая цена",
        description="Задайте либо цену, либо процент скидки.",
        json_schema_extra={"x-ui": {"widget": "number"}},
        default=None,
        gt=0,
    )
    decay_pct: FractionalPort | None = Field(
        title="Скидка, %",
        json_schema_extra={"x-ui": {"widget": "number"}},
        default=None,
        gt=0,
        lt=100,
    )
    current_price: NumericPort | None = Field(
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


def _target_price(ctx: RunContext[RepriceInput]) -> int:
    # Цена задаётся ЛИБО прямо, ЛИБО выводится из скидки — «одно из двух» схема поля не выражает,
    # поэтому выбор остаётся здесь; типы обоих путей уже проверены ею.
    if ctx.inputs.price is not None:
        return ctx.inputs.price

    decay_pct, current_price = ctx.inputs.decay_pct, ctx.inputs.current_price
    if decay_pct is None or current_price is None:
        raise RunFailed(
            ctx.run_id,
            ctx.node.id,
            "reprice needs either 'price' or both 'decay_pct' and 'current_price'",
        )
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

    async def execute(self, ctx: RunContext[RepriceInput]) -> StepResultDTO:
        item_id = ctx.inputs.item_id
        currency = ctx.inputs.currency

        # `_target_price` остаётся на сырых портах намеренно: цена задаётся ЛИБО прямо, ЛИБО
        # выводится из `decay_pct` + `current_price`, и «одно из двух» схема поля не выражает.
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
