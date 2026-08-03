"""A schedule is refused at creation if the scheduler could not parse it.

`POST /flows/{id}/triggers/create` takes a free-form cron string. Unvalidated, an unparseable one
was stored active and only failed in the worker — where it raised out of the startup sync and put
the process in a restart loop, so no tenant's flows ran at all, not just the one with the bad row.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.model import FlowId, TriggerKind
from app.domain.flow_engine.repo import FlowRepository
from app.domain.flow_engine.spec import FlowSpec, NodeSpec
from app.domain.triggers.errors import InvalidTriggerDefinition
from app.domain.triggers.repo import TriggerRepository
from app.domain.triggers.service import TriggerService


@pytest.fixture
async def service_and_flow(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'triggers.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = make_sessionmaker(engine)
    flows = FlowRepository(sm)
    tenant_id = TenantId(uuid4())
    node = NodeSpec(id="start", type="logic.get_my_lots", inputs={}, edges={})
    spec = FlowSpec(name="nightly", nodes=[node], entry_node_id="start")
    flow = await flows.create(tenant_id, "nightly", spec)
    yield TriggerService(flows, TriggerRepository(sm)), tenant_id, FlowId(flow.id)
    await engine.dispose()


async def test_a_cron_the_scheduler_cannot_parse_is_refused(service_and_flow) -> None:  # type: ignore[no-untyped-def]
    svc, tenant_id, flow_id = service_and_flow

    with pytest.raises(InvalidTriggerDefinition):
        await svc.create(
            tenant_id,
            flow_id,
            TriggerKind.SCHEDULE,
            schedule_cron="every four hours",
            event_type=None,
        )


async def test_a_valid_cron_still_goes_through(service_and_flow) -> None:  # type: ignore[no-untyped-def]
    svc, tenant_id, flow_id = service_and_flow

    trigger = await svc.create(
        tenant_id, flow_id, TriggerKind.SCHEDULE, schedule_cron="0 */4 * * *", event_type=None
    )

    assert trigger.schedule_cron == "0 */4 * * *"
