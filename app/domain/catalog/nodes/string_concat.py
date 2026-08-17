"""StringConcatNode — concatenates 2-3 resolved string parts (each independently wireable to a
literal or a `{{vars.x}}` ref via the existing compiler rewrite — no new templating engine)."""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class StringConcatInput(BaseSchema):
    # Union, а не `str`, потому что склеивать числа с текстом — обычное дело на холсте («куплено
    # N лотов»), а pydantic НЕ приводит число к строке даже в мягком режиме. Объявить `str`
    # значило бы отвергать вход, который узел до сих пор принимал и обязан принимать; приведение
    # остаётся в `execute`, где оно и было.
    a: str | int | float | bool = Field(
        title="Первая часть", json_schema_extra={"x-ui": {"widget": "text"}}
    )
    b: str | int | float | bool = Field(
        title="Вторая часть", json_schema_extra={"x-ui": {"widget": "text"}}
    )
    c: str | int | float | bool = Field(
        "", title="Третья часть", json_schema_extra={"x-ui": {"widget": "text"}}
    )


class StringConcatOutput(BaseSchema):
    result: str


class StringConcatNode(BaseNode):
    node_type = "logic.string_concat"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = StringConcatInput
    output_schema = StringConcatOutput
    required_inputs = ("a", "b")

    async def execute(self, ctx: RunContext[StringConcatInput]) -> StepResultDTO:
        parts = (ctx.inputs.a, ctx.inputs.b, ctx.inputs.c)
        return StepResultDTO(
            node_id=ctx.node.id, output={"result": "".join(str(part) for part in parts)}
        )
