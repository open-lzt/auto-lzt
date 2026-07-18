"""``FlowEventRouter`` — the lzt-eventus ``BaseConsumer`` that turns a matching DomainEvent into a
Run, dedup'd on ``run_key=f"{flow_id}:{event.seq}"``.

Subscribes statically to every ``FLOW_RELEVANT_EVENT_TYPES`` member (so registration never races
against ``triggers`` table content) and looks up the matching active EVENT triggers per event —
Postgres stays the single source of truth for "which flows want this event type", never a snapshot
cached at router construction time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.events.base import DomainEvent

from app.domain.events.types import FLOW_RELEVANT_EVENT_TYPES
from app.domain.flow_engine.model import Run, RunId, RunStatus
from app.domain.flow_engine.repo import FlowIrRepository, RunRepository
from app.domain.triggers.model import TriggerDefinition
from app.domain.triggers.repo import TriggerRepository

log = structlog.get_logger()


class FlowEventRouter(BaseConsumer):
    name = "flow-event-router"

    def __init__(
        self,
        *,
        triggers: TriggerRepository,
        runs: RunRepository,
        flow_irs: FlowIrRepository,
        enqueue_run: Callable[[RunId], Awaitable[None]],
    ) -> None:
        self.subscriptions: list[BaseSubscription[DomainEvent]] = [
            BaseSubscription(event_types=FLOW_RELEVANT_EVENT_TYPES)
        ]
        self._triggers = triggers
        self._runs = runs
        self._flow_irs = flow_irs
        self._enqueue_run = enqueue_run

    async def handle(self, event: DomainEvent) -> None:
        matches = await self._triggers.list_active_event_triggers(event.event_type)
        for trigger in matches:
            await self._fire(trigger, event)

    async def _fire(self, trigger: TriggerDefinition, event: DomainEvent) -> None:
        ir = await self._flow_irs.get_latest_for_flow(trigger.tenant_id, trigger.flow_id)
        if ir is None:
            log.warning(
                "flow_event_router.flow_not_compiled",
                flow_id=str(trigger.flow_id),
                trigger_id=str(trigger.id),
            )
            return

        run_key = f"{trigger.flow_id}:{event.seq}"
        now = datetime.now(UTC)
        run = Run(
            id=RunId(uuid4()),
            flow_id=trigger.flow_id,
            flow_ir_id=ir.id,
            tenant_id=trigger.tenant_id,
            run_key=run_key,
            status=RunStatus.PENDING,
            current_node_id=None,
            version=0,
            claimed_by=None,
            claimed_at=None,
            created_at=now,
            updated_at=now,
        )
        inserted = await self._runs.create_if_absent(run)
        stored = (
            run
            if inserted
            else await self._runs.get_by_key(trigger.tenant_id, trigger.flow_id, run_key)
        )
        if stored is None:  # pragma: no cover — the row exists by construction after DO NOTHING
            raise RuntimeError(f"event fire lost its row: run_key={run_key}")

        log.info(
            "flow_event_router.run_created" if inserted else "flow_event_router.run_deduped",
            run_id=str(stored.id),
            run_key=run_key,
            event_type=event.event_type.value,
        )
        if inserted:
            await self._enqueue_run(stored.id)
