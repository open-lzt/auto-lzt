"""GET /flows/{id}/status — the LiveBadge's data source. Seeds Flow/Run/Account rows directly via
the repos (same sqlite file the app's own lifespan-built engine opens) and drives the route over
httpx ASGI, matching the existing integration-test style (test_app_wiring.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId
from app.domain.account.repo import AccountRepository
from app.domain.flow_engine.model import FlowIrId, Run, RunId, RunStatus
from app.domain.flow_engine.repo import FlowRepository, RunRepository
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.main import create_app


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'status.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield db_url, make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


async def test_status_404_for_flow_owned_by_a_different_tenant(
    sqlite_app: tuple[str, object],
) -> None:
    _, sessionmaker = sqlite_app
    spec = FlowSpec(
        name="someone-elses-flow",
        nodes=[NodeSpec(id="n1", type="market.bump", inputs={"item_id": InputSpec(literal=1)})],
        entry_node_id="n1",
    )
    # Seeded under a random tenant, not the app's single default_tenant_id — the route must not
    # leak another tenant's flow (missing-tenant-filter is a silent killer, see code-quality rules).
    flow = await FlowRepository(sessionmaker).create(TenantId(uuid4()), spec.name, spec)  # type: ignore[arg-type]

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/flows/{flow.id}/status")
    assert resp.status_code == 404


async def test_status_reports_running_and_active_account_count(
    sqlite_app: tuple[str, object],
) -> None:
    _, sessionmaker = sqlite_app
    settings = get_settings()
    tenant_id = TenantId(UUID(settings.default_tenant_id))

    spec = FlowSpec(
        name="bump-flow",
        nodes=[NodeSpec(id="n1", type="market.bump", inputs={"item_id": InputSpec(literal=1)})],
        entry_node_id="n1",
    )
    flow = await FlowRepository(sessionmaker).create(tenant_id, spec.name, spec)  # type: ignore[arg-type]

    now = datetime.now(UTC)
    run = Run(
        id=RunId(uuid4()),
        flow_id=flow.id,
        flow_ir_id=FlowIrId(uuid4()),
        tenant_id=tenant_id,
        run_key="manual-1",
        status=RunStatus.COMPLETED,
        current_node_id=None,
        version=1,
        claimed_by="worker-1",
        claimed_at=now,
        created_at=now,
        updated_at=now,
    )
    await RunRepository(sessionmaker).create_if_absent(run)  # type: ignore[arg-type]

    async with sessionmaker() as session:  # type: ignore[operator]
        repo = AccountRepository(session)
        await repo.create(
            tenant_id,
            Account(
                id=AccountId(uuid4()),
                tenant_id=tenant_id,
                encrypted_token=b"enc",
                created_at=now,
                status=AccountStatus.ACTIVE,
            ),
        )
        await repo.create(
            tenant_id,
            Account(
                id=AccountId(uuid4()),
                tenant_id=tenant_id,
                encrypted_token=b"enc",
                created_at=now,
                status=AccountStatus.EXCLUDED,
            ),
        )
        await session.commit()

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/flows/{flow.id}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["active_accounts"] == 1
    assert body["last_run_at"] is not None


async def test_status_404_for_unknown_flow(sqlite_app: tuple[str, object]) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/flows/{uuid4()}/status")
    assert resp.status_code == 404
