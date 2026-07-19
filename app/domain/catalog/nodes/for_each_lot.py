"""ForEachLotNode — fans out over a lot-id list produced upstream (typically ``get-my-lots``).

Read-only routing: this node does not itself loop the interpreter — it emits the runtime's fan-out
marker (``__fanout_items__`` + ``__fanout_port__="item_id"``, see ``app/worker/runtime.py``'s
branching protocol) and the interpreter walks the ``"body"`` edge once per item, composing
``iteration_key`` as ``str(item_id)`` (or ``f"{parent_key}:{item_id}"`` when nested under
``for-each-account``) so ``RunStep``'s ``UNIQUE(run_id, node_id, iteration_key)`` persists resume
progress per lot, not per whole run (wave-04 spec).
"""

from __future__ import annotations

import json

from pydantic import Field, field_validator

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed

_FANOUT_PORT = "item_id"


class ForEachLotInput(BaseSchema):
    item_ids: str = Field(
        title="Лоты",
        description="JSON-массив id — обычно выход get_my_lots.",
        json_schema_extra={"x-ui": {"widget": "text"}},
    )  # JSON-encoded list[int] — see get-my-lots' output

    @field_validator("item_ids")
    @classmethod
    def _must_be_json_int_list(cls, value: str) -> str:
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not all(isinstance(v, int) for v in parsed):
            raise ValueError("item_ids must be a JSON array of ints")
        return value


class ForEachLotOutput(BaseSchema):
    count: int


class ForEachLotNode(BaseNode):
    node_type = "logic.for_each_lot"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = ForEachLotInput
    output_schema = ForEachLotOutput
    required_inputs = ("item_ids",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        raw = ctx.resolve_input("item_ids")
        if not isinstance(raw, str):
            raise RunFailed(ctx.run_id, ctx.node.id, f"item_ids must be a JSON string, got {raw!r}")
        try:
            item_ids = json.loads(raw)
        except ValueError as exc:
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"item_ids is not valid JSON: {raw!r}"
            ) from exc
        if not isinstance(item_ids, list) or not all(isinstance(v, int) for v in item_ids):
            raise RunFailed(ctx.run_id, ctx.node.id, "item_ids must decode to a JSON array of ints")

        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "__fanout_items__": json.dumps(item_ids),
                "__fanout_port__": _FANOUT_PORT,
                "count": len(item_ids),
            },
        )
