"""MathNode — basic arithmetic; div/mod-by-zero raises a typed MathDomainError."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import MathDomainError, RunFailed


class MathOp(StrEnum):
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    MOD = "mod"


class MathInput(BaseSchema):
    op: MathOp = Field(title="Операция", json_schema_extra={"ui": "select"})
    a: float = Field(title="Первый операнд", json_schema_extra={"ui": "number"})
    b: float = Field(title="Второй операнд", json_schema_extra={"ui": "number"})


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

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        op_raw = ctx.resolve_input("op")
        if not isinstance(op_raw, str) or op_raw not in MathOp:
            raise RunFailed(ctx.run_id, ctx.node.id, f"unknown math op {op_raw!r}")
        op = MathOp(op_raw)
        a_raw, b_raw = ctx.resolve_input("a"), ctx.resolve_input("b")
        if a_raw is None or b_raw is None:
            raise RunFailed(ctx.run_id, ctx.node.id, "math inputs must not be null")
        a, b = float(a_raw), float(b_raw)
        if op in (MathOp.DIV, MathOp.MOD) and b == 0:
            raise MathDomainError(op=op.value, a=a, b=b, reason="division by zero")
        result = a % b if op is MathOp.MOD else (a / b if op is MathOp.DIV else _OPS[op](a, b))
        return StepResultDTO(node_id=ctx.node.id, output={"result": result})
