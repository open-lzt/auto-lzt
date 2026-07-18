"""ConditionNode — read-only predicate routing via labelled edges ``"true"``/``"false"`` (F-12).

Not idempotent by definition (no side effect); never touches ``ctx.deps.guard``. The operator
vocabulary and its semantics live in ``operators.py``, shared with ``logic.compare``.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.nodes.operators import (
    ComparisonOp,
    InvalidPattern,
    compile_pattern,
    evaluate,
)
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import CompileError, RunFailed
from app.domain.flow_engine.ir_node import IRInput, LiteralValue


class ConditionInput(BaseSchema):
    left: str | int | float | bool = Field(title="Что сравниваем", json_schema_extra={"ui": "text"})
    op: ComparisonOp = Field(title="Операция", json_schema_extra={"ui": "select"})
    right: str | int | float | bool | None = Field(
        None,
        title="С чем сравниваем",
        description="Не нужен для is_null.",
        json_schema_extra={"ui": "text"},
    )


class ConditionOutput(BaseSchema):
    result: bool


def _literal(inputs: Mapping[str, IRInput], port: str) -> str | int | float | bool | None:
    """The port's compile-time value, or None when absent or arriving via a ref (unknowable)."""
    value = inputs.get(port)
    return value.value if isinstance(value, LiteralValue) else None


def validate_operands(
    node_id: str,
    inputs: Mapping[str, IRInput],
    *,
    op_port: str,
    pattern_port: str,
) -> None:
    """Compile-time operand checks shared by ``logic.condition`` and ``logic.compare``.

    Two things the compiler cannot see from ``required_inputs`` alone:

    1. ``right`` is required by every operator except ``is_null`` (which ignores it). Leaving it in
       ``required_inputs`` would force an is_null node to wire a dummy operand; dropping it there
       and checking here keeps a forgotten ``right`` on ``eq`` a compile error instead of silently
       evaluating to False under the null rule. When ``op`` arrives via a ref its value is unknown,
       so ``right`` is demanded conservatively.
    2. A malformed ``regex`` pattern fails at compile (400) rather than mid-run — but only when the
       pattern is a literal. Through a ref it is unknown until runtime and ``execute`` handles it.
    """
    op = _literal(inputs, op_port)
    if op != ComparisonOp.IS_NULL and pattern_port not in inputs:
        raise CompileError(f"missing required input '{pattern_port}'", node_id)

    pattern = _literal(inputs, pattern_port)
    if op != ComparisonOp.REGEX or not isinstance(pattern, str):
        return
    try:
        compile_pattern(pattern)
    except InvalidPattern as exc:
        raise CompileError(f"invalid regex {exc.pattern!r}: {exc.reason}", node_id) from exc


class ConditionNode(BaseNode):
    node_type = "logic.condition"
    required_inputs = ("left", "op")

    @classmethod
    def validate_compile(cls, node_id: str, inputs: Mapping[str, IRInput]) -> None:
        validate_operands(node_id, inputs, op_port="op", pattern_port="right")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        left = ctx.resolve_input("left")
        right = ctx.resolve_optional("right")
        op_raw = ctx.resolve_input("op")
        if not isinstance(op_raw, str) or op_raw not in ComparisonOp:
            raise RunFailed(ctx.run_id, ctx.node.id, f"unknown comparison op {op_raw!r}")
        op = ComparisonOp(op_raw)

        try:
            result = evaluate(op, left, right)
        except TypeError as exc:
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"cannot compare {left!r} {op.value} {right!r}"
            ) from exc
        except InvalidPattern as exc:
            # Only reachable when the pattern arrived through a ref; a literal is caught at compile.
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"invalid regex {exc.pattern!r}: {exc.reason}"
            ) from exc

        edge = "true" if result else "false"
        return StepResultDTO(node_id=ctx.node.id, output={"__edge__": edge, "result": result})
