"""TakeNode — the first N entries of a JSON id list, so a fan-out can be bounded.

Exists because ``for-each-lot`` fans out over everything it is given and no shipped node could cut
a list down first. That made "bump at most N lots per fire" inexpressible as a graph, which the
autobump preset needs and which any other fan-out will want the moment a seller has 500 lots.

Deliberately a generic list primitive rather than an autobump-shaped node: it knows nothing about
lots, bumps or schedules, so `get-my-lots -> take -> for-each-lot` and any future
`something-that-lists -> take -> loop` are the same shape.
"""

from __future__ import annotations

import json

from pydantic import Field, field_validator

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed


class TakeInput(BaseSchema):
    items: str = Field(
        title="Список",
        description="JSON-массив — обычно выход get_my_lots.",
        json_schema_extra={"x-ui": {"widget": "text"}},
    )
    count: int = Field(
        ge=0,
        title="Сколько взять",
        description="Сколько первых элементов оставить. Ноль — не брать ничего.",
        json_schema_extra={"x-ui": {"widget": "number"}},
    )

    @field_validator("items")
    @classmethod
    def _must_be_json_list(cls, value: str) -> str:
        if not isinstance(json.loads(value), list):
            raise ValueError("items must be a JSON array")
        return value


class TakeOutput(BaseSchema):
    items: str  # JSON-encoded, same element type as the input — feeds a for-each node
    count: int
    # True when the input was longer than the cap. The preset does not branch on it, but a flow that
    # wants to notify "N lots were skipped this fire" has no other way to know it happened.
    truncated: bool


def _as_count(ctx: RunContext, raw: object) -> int:
    """Zero and an integral float are both legal here, and both are load-bearing.

    ``logic.math`` types every result as ``float``, so a count computed on the canvas — the autobuy
    computes ``budget // max_price`` — arrives as ``3.0``. Refusing it made the whole graph
    uncompilable in practice while a hand-typed ``3`` worked, which is the worst kind of failure:
    only the derived path breaks.

    Zero means "the budget does not cover one lot at this ceiling". That is a green run with
    nothing taken, not an error — a scheduled autobuy that fails every fire until the price drops
    is an alarm nobody can act on.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise RunFailed(ctx.run_id, ctx.node.id, f"count must be a number, got {raw!r}")
    if isinstance(raw, float) and not raw.is_integer():
        raise RunFailed(ctx.run_id, ctx.node.id, f"count must be a whole number, got {raw!r}")
    count = int(raw)
    if count < 0:
        raise RunFailed(ctx.run_id, ctx.node.id, f"count must not be negative, got {raw!r}")
    return count


class TakeNode(BaseNode):
    node_type = "logic.take"
    category = NodeCategory.LOGIC
    idempotent = True
    capabilities = PURE
    input_schema = TakeInput
    output_schema = TakeOutput
    required_inputs = ("items", "count")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        raw = ctx.resolve_input("items")
        if not isinstance(raw, str):
            raise RunFailed(ctx.run_id, ctx.node.id, f"items must be a JSON string, got {raw!r}")
        try:
            items = json.loads(raw)
        except ValueError as exc:
            raise RunFailed(ctx.run_id, ctx.node.id, f"items is not valid JSON: {raw!r}") from exc
        if not isinstance(items, list):
            raise RunFailed(ctx.run_id, ctx.node.id, "items must decode to a JSON array")

        count = _as_count(ctx, ctx.resolve_input("count"))
        kept = items[:count]
        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "items": json.dumps(kept),
                "count": len(kept),
                "truncated": len(items) > len(kept),
            },
        )
