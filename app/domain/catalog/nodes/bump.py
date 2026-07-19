"""BumpNode — thin wrapper over ``MarketAdapter.bump`` / ``pylzt.Client.market.managing_bump``.

Formalizes the Wave-3 registry stub as a catalog entry: same behaviour, now with typed
input/output schemas and per-account pinning (decision #18) via ``ctx.active_account_id``.
"""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_MUTATE_MONEY, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class BumpInput(BaseSchema):
    item_id: int = Field(title="Лот", json_schema_extra={"x-ui": {"widget": "lot_ref"}}, gt=0)


class BumpOutput(BaseSchema):
    item_id: int
    bumped_at: str  # ISO-8601, UTC


def _as_int(value: str | int | float | bool | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"item_id must be an int, got {value!r}")
    return int(value)


class BumpNode(BaseNode):
    node_type = "market.bump"
    category = NodeCategory.ACTION
    idempotent = True
    # MONEY: must call guard.check_and_set before the effect; a contract test enforces it.
    capabilities = MARKET_MUTATE_MONEY
    input_schema = BumpInput
    output_schema = BumpOutput
    required_inputs = ("item_id",)
    batchable = True

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        item_id = _as_int(ctx.resolve_input("item_id"))
        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            return StepResultDTO(
                node_id=ctx.node.id, output={"item_id": item_id, "deduplicated": True}
            )

        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is not None:
            account = await ctx.deps.load_account(ctx.tenant_id, account_ref)
            result = await ctx.deps.market.bump(item_id, account)
        else:
            result = await ctx.deps.market.bump_via_pool(ctx.tenant_id, item_id)

        return StepResultDTO(
            node_id=ctx.node.id,
            output={"item_id": result.item_id, "bumped_at": result.bumped_at.isoformat()},
        )
