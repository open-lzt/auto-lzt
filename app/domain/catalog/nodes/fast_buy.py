"""FastBuyNode — buys one lot by id. The only node in the catalog that spends money.

Sits behind ``for-each-lot``, so it receives one id per iteration and never sees the list. The
price ceiling is enforced upstream by ``market.search``'s ``max_price``: this node is handed an id
that already passed the filter, which is why it takes no price of its own — a second ceiling here
would be a second source of truth for the same rule.

``dry_run`` defaults to TRUE. A buy node that defaulted to spending would turn a mistyped flow into
a purchase, and the whole point of the testnet-first stance is that the expensive default is opt-in.
Like every MONEY node it consumes its idempotency key before the effect, so a resumed run never
buys the same lot twice.
"""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_MUTATE_MONEY, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class FastBuyInput(BaseSchema):
    item_id: int = Field(gt=0, title="Лот", json_schema_extra={"x-ui": {"widget": "lot_ref"}})
    dry_run: bool = Field(
        default=True,
        title="Холостой прогон",
        description="Включено — покупка не выполняется, узел только сообщает что купил бы.",
        json_schema_extra={"x-ui": {"widget": "switch"}},
    )


class FastBuyOutput(BaseSchema):
    item_id: int
    price: int
    purchased: bool


def _as_int(value: str | int | float | bool | None, port: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{port} must be an int, got {value!r}")
    return int(value)


def _as_bool(value: str | int | float | bool | None, port: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return bool(value)
    raise ValueError(f"{port} must be a bool, got {value!r}")


class FastBuyNode(BaseNode):
    node_type = "market.fast_buy"
    category = NodeCategory.ACTION
    idempotent = True
    # MONEY: must call guard.check_and_set before the effect; a contract test enforces it.
    capabilities = MARKET_MUTATE_MONEY
    input_schema = FastBuyInput
    output_schema = FastBuyOutput
    required_inputs = ("item_id",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        item_id = _as_int(ctx.resolve_input("item_id"), "item_id")
        raw_dry_run = ctx.resolve_input("dry_run")
        dry_run = True if raw_dry_run is None else _as_bool(raw_dry_run, "dry_run")

        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            return StepResultDTO(
                node_id=ctx.node.id,
                output={"item_id": item_id, "price": 0, "purchased": False, "deduplicated": True},
            )

        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is not None:
            account = await ctx.deps.load_account(ctx.tenant_id, account_ref)
            result = await ctx.deps.market.fast_buy(item_id, account, dry_run=dry_run)
        else:
            result = await ctx.deps.market.fast_buy_via_pool(
                ctx.tenant_id, item_id, dry_run=dry_run
            )

        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "item_id": result.item_id,
                "price": result.price,
                "purchased": result.purchased,
            },
        )
