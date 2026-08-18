"""MathNode — basic arithmetic; div/mod-by-zero raises a typed MathDomainError."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.core.schema import BaseSchema, FractionalPort
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import MathDomainError


class MathOp(StrEnum):
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    MOD = "mod"
    # Floor division, and the reason it exists is a budget: "how many lots at `max_price` fit into
    # `budget`" is the autobuy's spend ceiling, and it must be a whole number because the node that
    # consumes it (`logic.take`) refuses a fractional count. Without this, `1000 / 300` fails every
    # run — while `900 / 300` passes, so the defect hides behind round numbers.
    IDIV = "idiv"


class MathInput(BaseSchema):
    op: MathOp = Field(title="Операция", json_schema_extra={"x-ui": {"widget": "select"}})
    a: FractionalPort = Field(
        title="Первый операнд", json_schema_extra={"x-ui": {"widget": "number"}}
    )
    b: FractionalPort = Field(
        title="Второй операнд", json_schema_extra={"x-ui": {"widget": "number"}}
    )


class MathOutput(BaseSchema):
    result: float


_OPS = {
    MathOp.ADD: lambda a, b: a + b,
    MathOp.SUB: lambda a, b: a - b,
    MathOp.MUL: lambda a, b: a * b,
}


class MathNode(BaseNode):
    node_type = "logic.math"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = MathInput
    output_schema = MathOutput
    required_inputs = ("op", "a", "b")

    async def execute(self, ctx: RunContext[MathInput]) -> StepResultDTO:
        # Операция, операнды и их обязательность — уже проверены схемой при сборке контекста.
        op, a, b = ctx.inputs.op, ctx.inputs.a, ctx.inputs.b
        if op in (MathOp.DIV, MathOp.MOD, MathOp.IDIV) and b == 0:
            raise MathDomainError(op=op.value, a=a, b=b, reason="division by zero")
        match op:
            case MathOp.MOD:
                result = a % b
            case MathOp.DIV:
                result = a / b
            case MathOp.IDIV:
                result = float(a // b)
            case _:
                result = _OPS[op](a, b)
        return StepResultDTO(node_id=ctx.node.id, output={"result": result})
