"""Flow live-status route — powers the canvas LiveBadge ("running 24/7 · N accounts"), polled every
five seconds by the frontend.

Reimplemented as a thin read over the task projection. Three things were wrong, and all of them
were ours:

1. **It reported the wrong answer.** ``_LIVE_STATUSES`` was ``{PENDING, RUNNING, COMPLETED}`` and
   the test was ``any(...)`` over the flow's ENTIRE run history, so a flow whose only run completed
   successfully last month reported ``running=true`` forever. Its own comment admitted the shortcut.
   Liveness now comes from the LATEST run and shares ``_RUNNING_STATUSES`` with ``TaskHealth`` — one
   definition of "is this running", rather than two endpoints answering it differently.

2. **It was N+1 on a five-second poll.** It loaded every run of the flow to answer a yes/no question
   and every account of the tenant to answer a count. Both are now bounded reads.

3. **``last_run_at`` depended on an unspecified order.** It took ``runs[0]`` from a query with no
   ``ORDER BY``, so "the last run" was whatever the database happened to return first. The
   replacement orders explicitly.

The response SHAPE is unchanged, so this is a semantics fix behind a stable contract, not a breaking
change: LiveBadge and its existing tests keep working untouched.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.db.base import session_scope
from app.domain.account.model import TenantId
from app.domain.account.repo import AccountRepository
from app.domain.flow_engine.errors import EntityNotFound
from app.domain.flow_engine.model import FlowId
from app.domain.flow_engine.repo import FlowRepository
from app.domain.tasks.repo import TaskRepository
from app.domain.tasks.service import TaskService

router = APIRouter(prefix="/flows", tags=["flows"])


class FlowStatusDTO(BaseSchema):
    running: bool
    active_accounts: int
    last_run_at: datetime | None


@router.get("/{flow_id}/status")
async def get_flow_status(
    flow_id: UUID,
    request: Request,
    tenant_id: TenantId = Depends(tenant_id_dep),
) -> FlowStatusDTO:
    sessionmaker = request.app.state.sessionmaker

    fid = FlowId(flow_id)
    flow = await FlowRepository(sessionmaker).get(tenant_id, fid)
    if flow is None:
        raise EntityNotFound("flow", str(flow_id))

    running, last_run_at = await TaskService(TaskRepository(sessionmaker)).flow_liveness(
        tenant_id, fid
    )
    async with session_scope(sessionmaker) as session:
        active_accounts = await AccountRepository(session).count_active(tenant_id)

    return FlowStatusDTO(running=running, active_accounts=active_accounts, last_run_at=last_run_at)
