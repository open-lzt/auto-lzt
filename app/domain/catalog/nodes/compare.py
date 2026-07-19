"""CompareNode — comparison over two resolved inputs (numeric-coerced, string fallback).

Shares its operator vocabulary and semantics with ``logic.condition`` via ``operators.py``; the
difference between the two nodes is input handling, not the operators — compare numeric-coerces its
operands first so ``"10" > "9"`` orders as numbers rather than lexically.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.catalog.nodes.condition import validate_operands
from app.domain.catalog.nodes.operators import ComparisonOp, InvalidPattern, evaluate
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.ir_node import IRInput

_Scalar = str | int | float | bool


def _coerce_numeric(value: _Scalar) -> _Scalar:
    if isinstance(value, bool | int | float):
        return value
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


class CompareInput(BaseSchema):
    op: ComparisonOp = Field(title="Операция", json_schema_extra={"x-ui": {"widget": "select"}})
    a: _Scalar = Field(title="Первый операнд", json_schema_extra={"x-ui": {"widget": "text"}})
    b: _Scalar | None = Field(
        None,
        title="Второй операнд",
        description="Не нужен для is_null.",
        json_schema_extra={"x-ui": {"widget": "text"}},
    )


class CompareOutput(BaseSchema):
    result: bool


class CompareNode(BaseNode):
    node_type = "logic.compare"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = CompareInput
    output_schema = CompareOutput
    required_inputs = ("op", "a")

    @classmethod
    def validate_compile(cls, node_id: str, inputs: Mapping[str, IRInput]) -> None:
        validate_operands(node_id, inputs, op_port="op", pattern_port="b")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        op_raw = ctx.resolve_input("op")
        if not isinstance(op_raw, str) or op_raw not in ComparisonOp:
            raise RunFailed(ctx.run_id, ctx.node.id, f"unknown comparison op {op_raw!r}")
        op = ComparisonOp(op_raw)

        a_raw, b_raw = ctx.resolve_input("a"), ctx.resolve_optional("b")
        # A null operand no longer fails the run: it evaluates to False for every operator except
        # is_null, which is the only way to test for one (see operators.py's null semantics).
        a = _coerce_numeric(a_raw) if a_raw is not None else None
        b = _coerce_numeric(b_raw) if b_raw is not None else None

        try:
            result = evaluate(op, a, b)
        except TypeError:
            result = evaluate(op, str(a), str(b))
        except InvalidPattern as exc:
            # Only reachable when the pattern arrived through a ref; a literal is caught at compile.
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"invalid regex {exc.pattern!r}: {exc.reason}"
            ) from exc

        return StepResultDTO(node_id=ctx.node.id, output={"result": result})
