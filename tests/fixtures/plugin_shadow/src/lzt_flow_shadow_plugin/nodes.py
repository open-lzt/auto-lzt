"""A hostile plugin: it claims ``market.bump``.

This is the attack the collision rule exists for. market.bump spends money, and a plugin that
quietly replaced it would have every flow on the stand calling this code instead — with no error,
no log line, and nothing in the UI to see. Declaring PURE while claiming a money node is part of
the act: capabilities are self-declared, which is exactly why shadowing must be impossible rather
than merely discouraged.
"""

from __future__ import annotations

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class EvilInput(BaseSchema):
    item_id: int


class EvilOutput(BaseSchema):
    item_id: int


class EvilBumpNode(BaseNode):
    node_type = "market.bump"
    required_inputs = ("item_id",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        return StepResultDTO(node_id=ctx.node.id, output={"item_id": 0})


REGISTRATIONS = [
    NodeRegistration(
        node_type=NodeType(
            key=EvilBumpNode.node_type,
            category=NodeCategory.ACTION,
            input_schema=EvilInput,
            output_schema=EvilOutput,
            idempotent=True,
            capabilities=frozenset({NodeCapability.PURE}),
        ),
        impl=EvilBumpNode,
    )
]
