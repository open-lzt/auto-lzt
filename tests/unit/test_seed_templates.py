"""Seed template flows compile, the loader is idempotent, and /catalog/categories mirrors pylzt."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pylzt
import pytest

from app.api.catalog_routes import list_market_categories
from app.domain.account.model import TenantId
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.model import Flow, FlowId
from app.domain.flow_engine.spec import FlowSpec
from seeds.load_templates import TEMPLATES_DIR, load_seed_templates, load_specs
from tests.fixtures.flow_fakes import node_classes


def _template_files() -> list[Path]:
    return sorted(TEMPLATES_DIR.glob("*.json"))


def test_templates_present() -> None:
    assert len(_template_files()) >= 8


@pytest.mark.parametrize("path", _template_files(), ids=lambda p: p.stem)
def test_template_compiles(path: Path) -> None:
    spec = FlowSpec.model_validate_json(path.read_text(encoding="utf-8"))
    flow = Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )
    ir = compile_flow(flow, node_classes())
    assert len(ir.nodes) == len(spec.nodes)


class _FakeFlowRepo:
    def __init__(self) -> None:
        self._by_tenant: dict[TenantId, list[Flow]] = {}

    async def list(self, tenant_id: TenantId) -> list[Flow]:
        return list(self._by_tenant.get(tenant_id, []))

    async def create(self, tenant_id: TenantId, name: str, spec: FlowSpec) -> Flow:
        flow = Flow(
            id=FlowId(uuid4()),
            tenant_id=tenant_id,
            name=name,
            version=1,
            spec=spec,
            created_at=datetime.now(UTC),
        )
        self._by_tenant.setdefault(tenant_id, []).append(flow)
        return flow


async def test_loader_is_idempotent() -> None:
    repo = _FakeFlowRepo()
    tenant = TenantId(uuid4())
    total = len(load_specs())

    first = await load_seed_templates(repo, tenant)  # type: ignore[arg-type]
    assert len(first.created) == total
    assert first.skipped == ()

    second = await load_seed_templates(repo, tenant)  # type: ignore[arg-type]
    assert second.created == ()
    assert len(second.skipped) == total

    assert len(await repo.list(tenant)) == total  # one row per template, not duplicated


async def test_categories_endpoint_mirrors_pylzt() -> None:
    result = await list_market_categories()
    assert {c.slug for c in result} == {c.value for c in pylzt.Category}
    assert all(c.label for c in result)
