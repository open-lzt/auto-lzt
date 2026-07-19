"""WaitUntilNode — re-entrant self-loop until `condition` resolves true or `timeout_s` elapses.

The only node type needing runtime-loop support (runtime.py's self-loop protocol): each revisit
gets a fresh RunStep via a new iteration_key, and `ctx.loop_iteration` (0-based revisit count) lets
this node bound its own wait deterministically without persisted wall-clock state — elapsed time is
`loop_iteration * poll_interval_s`, exact because each cycle actually sleeps poll_interval_s before
looping. Compiler wiring: this node's own edge label "wait" must point back to its own node id.
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed, WaitTimeoutError

_WAIT_EDGE = "wait"
_DONE_EDGE = "done"


class WaitUntilInput(BaseSchema):
    condition: bool = Field(title="Условие", json_schema_extra={"ui": "bool"})
    poll_interval_s: int = Field(title="Интервал опроса, с", json_schema_extra={"ui": "number"})
    timeout_s: int = Field(title="Таймаут, с", json_schema_extra={"ui": "number"})


class WaitUntilOutput(BaseSchema):
    result: bool


class WaitUntilNode(BaseNode):
    node_type = "logic.wait_until"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    input_schema = WaitUntilInput
    output_schema = WaitUntilOutput
    required_inputs = ("condition", "poll_interval_s", "timeout_s")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        condition = bool(ctx.resolve_input("condition"))
        poll_raw, timeout_raw = (
            ctx.resolve_input("poll_interval_s"),
            ctx.resolve_input("timeout_s"),
        )
        if poll_raw is None or timeout_raw is None:
            raise RunFailed(ctx.run_id, ctx.node.id, "poll_interval_s/timeout_s must not be null")
        poll_interval_s, timeout_s = int(poll_raw), int(timeout_raw)

        if condition:
            return StepResultDTO(
                node_id=ctx.node.id, output={"__edge__": _DONE_EDGE, "result": True}
            )

        elapsed_s = ctx.loop_iteration * poll_interval_s
        if elapsed_s >= timeout_s:
            raise WaitTimeoutError(ctx.node.id, timeout_s)

        await asyncio.sleep(poll_interval_s)
        return StepResultDTO(node_id=ctx.node.id, output={"__edge__": _WAIT_EDGE, "result": False})
