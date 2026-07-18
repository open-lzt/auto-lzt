"""StringConcatNode — concatenates 2-3 resolved string parts (each independently wireable to a
literal or a `{{vars.x}}` ref via the existing compiler rewrite — no new templating engine)."""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class StringConcatInput(BaseSchema):
    a: str = Field(title="Первая часть", json_schema_extra={"ui": "text"})
    b: str = Field(title="Вторая часть", json_schema_extra={"ui": "text"})
    c: str = Field("", title="Третья часть", json_schema_extra={"ui": "text"})


class StringConcatOutput(BaseSchema):
    result: str


class StringConcatNode(BaseNode):
    node_type = "logic.string_concat"
    required_inputs = ("a", "b")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        a = str(ctx.resolve_input("a"))
        b = str(ctx.resolve_input("b"))
        c = str(ctx.resolve_optional("c") or "")
        return StepResultDTO(node_id=ctx.node.id, output={"result": a + b + c})
