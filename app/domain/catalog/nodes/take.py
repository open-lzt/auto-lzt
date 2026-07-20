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
        ge=1,
        title="Сколько взять",
        description="Сколько первых элементов оставить.",
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

        count = ctx.resolve_input("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise RunFailed(ctx.run_id, ctx.node.id, f"count must be a positive int, got {count!r}")

        kept = items[:count]
        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "items": json.dumps(kept),
                "count": len(kept),
                "truncated": len(items) > len(kept),
            },
        )
