"""BatchStatusNode — lists pylzt's own persisted batch-job records (audit/history of requests
the client has sent via `execute_batch`/`job`), confirmed real signature:
`batch_job_history(*, only_pending=False, limit=None, offset=0) -> list[BatchJobRecord]`.
Each `BatchJobRecord` carries `record_id`/`job`/`result`/`committed`. `commit`/`delete` are
exposed as optional kwargs here rather than two more node types (judgment call, avoids catalog
bloat for what's really "what to do once a batch's status is known")."""

from __future__ import annotations

import json

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_READ, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class BatchStatusInput(BaseSchema):
    only_pending: bool = Field(
        False, title="Только незавершённые", json_schema_extra={"ui": "bool"}
    )
    limit: int | None = Field(None, title="Сколько вернуть", json_schema_extra={"ui": "number"})
    offset: int = Field(0, title="Смещение", json_schema_extra={"ui": "number"})
    commit_record_ids: str = Field(
        "",
        title="Подтвердить записи",
        description="JSON-массив record_id; пусто — ничего не подтверждать.",
        json_schema_extra={"ui": "text"},
    )  # JSON array of record_ids to commit, "" = none
    delete_record_ids: str = Field(
        "",
        title="Удалить записи",
        description="JSON-массив record_id; пусто — ничего не удалять.",
        json_schema_extra={"ui": "text"},
    )  # JSON array of record_ids to delete, "" = none


class BatchStatusOutput(BaseSchema):
    records: str  # JSON-encoded [{"record_id": ..., "committed": ..., "has_result": ...}]


class BatchStatusNode(BaseNode):
    node_type = "logic.batch_status"
    category = NodeCategory.LOGIC
    idempotent = False
    capabilities = MARKET_READ
    input_schema = BatchStatusInput
    output_schema = BatchStatusOutput

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        only_pending = bool(ctx.resolve_optional("only_pending") or False)
        limit_raw = ctx.resolve_optional("limit")
        limit = int(limit_raw) if limit_raw is not None else None
        offset = int(ctx.resolve_optional("offset") or 0)
        commit_ids: list[str] = json.loads(str(ctx.resolve_optional("commit_record_ids") or "[]"))
        delete_ids: list[str] = json.loads(str(ctx.resolve_optional("delete_record_ids") or "[]"))

        account_ref = ctx.active_account_id or ctx.node.account_ref
        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            if commit_ids:
                await client.commit_batch_jobs(commit_ids)
            if delete_ids:
                await client.delete_batch_jobs(delete_ids)
            history = await client.batch_job_history(
                only_pending=only_pending, limit=limit, offset=offset
            )

        records = [
            {
                "record_id": record.record_id,
                "committed": record.committed,
                "has_result": record.result is not None,
            }
            for record in history
        ]
        return StepResultDTO(node_id=ctx.node.id, output={"records": json.dumps(records)})
