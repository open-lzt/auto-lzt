"""A community node, written the way a third party would write one.

It imports nothing private: BaseNode, the registration types and the capability vocabulary are the
whole public surface a plugin needs. If this file ever has to reach into an underscore-prefixed
name to work, the extension point is not really an extension point.
"""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class ShoutInput(BaseSchema):
    text: str = Field(title="Текст", json_schema_extra={"ui": "text"})


class ShoutOutput(BaseSchema):
    shouted: str


class ShoutNode(BaseNode):
    node_type = "demo.shout"
    required_inputs = ("text",)
    batchable = False

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        text = str(ctx.resolve_input("text"))
        return StepResultDTO(node_id=ctx.node.id, output={"shouted": text.upper()})


REGISTRATIONS = [
    NodeRegistration(
        node_type=NodeType(
            key=ShoutNode.node_type,
            category=NodeCategory.LOGIC,
            input_schema=ShoutInput,
            output_schema=ShoutOutput,
            idempotent=True,
            capabilities=frozenset({NodeCapability.PURE}),
        ),
        impl=ShoutNode,
        # No origin: the loader stamps it. A plugin claiming to be a built-in would be the point.
    )
]
