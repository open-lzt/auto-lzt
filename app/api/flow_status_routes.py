"""Flow live-status route — powers the canvas LiveBadge ("running 24/7 · N accounts"), polled
every 5s by the frontend rather than pushed over a socket (MVP-scale tradeoff, wave-06 §Logic)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.schema import BaseSchema
from app.core.tenant import tenant_id_dep
from app.db.base import session_scope
from app.domain.account.model import AccountStatus, TenantId
from app.domain.account.repo import AccountRepository
from app.domain.flow_engine.errors import EntityNotFound
from app.domain.flow_engine.model import FlowId, RunStatus
from app.domain.flow_engine.repo import FlowRepository, RunRepository

router = APIRouter(prefix="/flows", tags=["flows"])

# A FAILED-only history reads as "not running"; anything else means the flow is live or has
# produced at least one non-failed run — good enough for the MVP demo badge (wave-06 §Logic).
_LIVE_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING, RunStatus.COMPLETED})


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

    runs = await RunRepository(sessionmaker).list_by_flow(tenant_id, fid)
    async with session_scope(sessionmaker) as session:
        accounts = await AccountRepository(session).list(tenant_id)

    return FlowStatusDTO(
        running=any(run.status in _LIVE_STATUSES for run in runs),
        active_accounts=sum(1 for a in accounts if a.status is AccountStatus.ACTIVE),
        last_run_at=runs[0].created_at if runs else None,
    )
