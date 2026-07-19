"""BatchListPendingNode — lists batch jobs still pending via the confirmed real signature
`iter_pending_batch_jobs(*, poll_interval=1.0, page_size=50, stop_when_empty=False) ->
AsyncIterator[BatchJobRecord]`. `stop_when_empty=True` so a single call drains what's
currently pending instead of polling forever."""

from __future__ import annotations

import json

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_READ, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class BatchListPendingOutput(BaseSchema):
    record_ids: str  # JSON array of pending batch job record_ids


class BatchListPendingNode(BaseNode):
    node_type = "logic.batch_list_pending"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = MARKET_READ
    output_schema = BatchListPendingOutput

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        account_ref = ctx.active_account_id or ctx.node.account_ref
        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            record_ids = [
                record.record_id
                async for record in client.iter_pending_batch_jobs(stop_when_empty=True)
            ]
        return StepResultDTO(node_id=ctx.node.id, output={"record_ids": json.dumps(record_ids)})
