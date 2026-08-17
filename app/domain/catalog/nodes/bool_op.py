"""BoolOpNode — boolean and/or/not over two (or one, for not) resolved inputs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class BoolOp(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"


class BoolOpInput(BaseSchema):
    op: BoolOp = Field(title="Операция", json_schema_extra={"x-ui": {"widget": "select"}})
    a: bool = Field(title="Первый операнд", json_schema_extra={"x-ui": {"widget": "bool"}})
    b: bool | None = Field(
        None,
        title="Второй операнд",
        description="Не нужен для NOT.",
        json_schema_extra={"x-ui": {"widget": "bool"}},
    )


class BoolOpOutput(BaseSchema):
    result: bool


class BoolOpNode(BaseNode):
    node_type = "logic.bool_op"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = BoolOpInput
    output_schema = BoolOpOutput
    required_inputs = ("op", "a")

    async def execute(self, ctx: RunContext[BoolOpInput]) -> StepResultDTO:
        # Операнды приходят уже разобранными схемой (`a: bool`). Прежний код звал `bool(...)` на
        # сыром значении, а питоновская истинность считает `"false"` и `"нет"` за ИСТИНУ — узел
        # молча возвращал True на любой непустой строке. Теперь такая строка отвергается.
        op, a = ctx.inputs.op, ctx.inputs.a
        if op is BoolOp.NOT:
            result = not a
        else:
            b = bool(ctx.inputs.b)
            result = (a and b) if op is BoolOp.AND else (a or b)
        return StepResultDTO(node_id=ctx.node.id, output={"result": result})
