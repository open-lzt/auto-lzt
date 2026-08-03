"""One automation per preset per tenant, enforced by the database.

`FlowService.deploy_from_preset` reads before it writes, so two deploys of the same preset can
both see "nothing there" and both insert — a double-click on the enable button is enough. The
partial unique index is what actually holds the rule; these tests pin that it holds, and that it
surfaces as a typed error the deploy path can turn into an edit rather than a 500.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import TenantId
from app.domain.flow_engine.errors import DuplicatePresetFlow
from app.domain.flow_engine.repo import FlowRepository
from app.domain.flow_engine.spec import FlowSpec, NodeSpec


def _spec(name: str) -> FlowSpec:
    node = NodeSpec(id="start", type="logic.get_my_lots", inputs={}, edges={})
    return FlowSpec(name=name, nodes=[node], entry_node_id="start")


@pytest.fixture
async def flows(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'preset.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield FlowRepository(make_sessionmaker(engine))
    await engine.dispose()


async def test_a_second_automation_for_one_preset_is_refused(flows) -> None:  # type: ignore[no-untyped-def]
    tenant_id = TenantId(uuid4())
    spec = _spec("autobump")

    await flows.create(tenant_id, spec.name, spec, source_preset_key="autobump")

    with pytest.raises(DuplicatePresetFlow):
        await flows.create(tenant_id, spec.name, spec, source_preset_key="autobump")


async def test_canvas_flows_are_unaffected_by_the_index(flows) -> None:  # type: ignore[no-untyped-def]
    """The index is partial on a non-NULL preset key — a tenant may keep as many hand-drawn flows
    as it likes, including several with the same name."""
    tenant_id = TenantId(uuid4())
    spec = _spec("my flow")

    await flows.create(tenant_id, spec.name, spec)
    await flows.create(tenant_id, spec.name, spec)

    assert len(await flows.list(tenant_id)) == 2


async def test_the_same_preset_in_another_tenant_is_allowed(flows) -> None:  # type: ignore[no-untyped-def]
    spec = _spec("autobump")

    await flows.create(TenantId(uuid4()), spec.name, spec, source_preset_key="autobump")
    await flows.create(TenantId(uuid4()), spec.name, spec, source_preset_key="autobump")
