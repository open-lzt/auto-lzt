"""JoinNode — the convergence point every ForkNode branch must reach. Its own `.execute()` is
never actually invoked by the interpreter: `_run_chain`'s `stop_before_types` mechanism (D2-1 fix)
halts each branch structurally right before a "logic.join"-typed node, and `_run_fork` synthesizes
the merged StepResultDTO itself (see runtime.py's `_run_fork`). This class exists so the node
registers, compiles, and shows correctly in the catalog — `execute()` raising is a deliberate
"this should never actually run" guard, not a stub."""

from __future__ import annotations

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class JoinOutput(BaseSchema):
    branches: str  # JSON-encoded {branch_label: {...that branch's terminal output...}}


class JoinNode(BaseNode):
    node_type = "logic.join"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = PURE
    output_schema = JoinOutput

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        raise AssertionError(
            "JoinNode.execute() should never be called directly — the interpreter's fork/join "
            "walk (_run_fork) synthesizes its result; a join node reached outside a fork means "
            "the flow references 'logic.join' without a preceding ForkNode."
        )
