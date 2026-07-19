"""SwitchNode — generalized ConditionNode: N-way routing by matching `value` against a typed,
UI-composed case list (Inspector renders a case-editor widget, never a hand-typed JSON field —
the fix is in authoring, not the wire shape, which stays one flat `cases` JSON-string field like
`get_my_lots.py`'s `item_ids`). No-match fails loud via a typed error, never a silent fallthrough.
"""

from __future__ import annotations

import json

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import NoMatchingCase


class SwitchInput(BaseSchema):
    value: str = Field(title="Значение", json_schema_extra={"ui": "text"})
    cases: str = Field(
        title="Ветки",
        description="JSON-объект {метка_ребра: ожидаемое_значение}.",
        json_schema_extra={"ui": "text"},
    )  # JSON-encoded dict[str, str] — {edge_label: expected_value}


class SwitchOutput(BaseSchema):
    result_edge: str


class SwitchNode(BaseNode):
    node_type = "logic.switch"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = SwitchInput
    output_schema = SwitchOutput
    required_inputs = ("value", "cases")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        value = str(ctx.resolve_input("value"))
        cases: dict[str, str] = json.loads(str(ctx.resolve_input("cases")))
        for label, expected in cases.items():
            if value == expected:
                return StepResultDTO(
                    node_id=ctx.node.id, output={"__edge__": label, "result_edge": label}
                )
        raise NoMatchingCase(ctx.node.id, value, tuple(cases.keys()))
