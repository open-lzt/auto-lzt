"""ForkNode — zero configurable inputs; the user just draws N edges out of it on canvas (the
existing NodeSpec.edges mechanism every node already has). Emits the reserved "__fork__" output
key telling the interpreter to walk every edge concurrently instead of picking one."""

from __future__ import annotations

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class ForkOutput(BaseSchema):
    fork: bool = True  # reserved key "__fork__" in the actual StepResultDTO.output


class ForkNode(BaseNode):
    node_type = "logic.fork"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    output_schema = ForkOutput

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        return StepResultDTO(node_id=ctx.node.id, output={"__fork__": True})
