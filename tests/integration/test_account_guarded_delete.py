"""Account label, list, and guarded delete: a delete is refused (not cascaded) while a flow with
a LIVE schedule trigger still pins the account somewhere in its spec — including nested under a
batch node's ``children``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.db.models  # noqa: F401 — registers ORM models on Base.metadata
from app.core.config import get_settings
from app.db.base import Base, make_engine, make_sessionmaker, session_scope
from app.db.models import TriggerORM
from app.domain.account.model import AccountId
from app.domain.account.repo import _spec_references
from app.domain.flow_engine.model import TriggerKind
from app.main import create_app


@pytest.fixture(autouse=True)
def _market_double(mock_lzt: object) -> None:
    """Registering an account now verifies its token against the marketplace, so every test here
    needs the double — without it the call leaves the process and comes back 401."""


@pytest.fixture
async def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'account_delete.db'}"
    monkeypatch.setenv("LZT_FLOW_DATABASE_URL", db_url)
    get_settings.cache_clear()
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield make_sessionmaker(make_engine(db_url))
    get_settings.cache_clear()


def _node(
    node_id: str, *, account_ref: str | None = None, children: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    node: dict[str, Any] = {"id": node_id, "type": "logic.math", "inputs": {}}
    if account_ref is not None:
        node["account_ref"] = account_ref
    if children is not None:
        node["children"] = children
    return node


def _flow_spec(name: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "entry_node_id": nodes[0]["id"], "params": [], "nodes": nodes}


async def _create_account(client: httpx.AsyncClient, token: str) -> str:
    resp = await client.post("/accounts/create", json={"token": token})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _create_flow(client: httpx.AsyncClient, spec: dict[str, Any]) -> str:
    resp = await client.post("/flows/create", json=spec)
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["flow_id"])


async def _add_trigger(sessionmaker: Any, tenant_id: UUID, flow_id: str, *, active: bool) -> None:
    async with session_scope(sessionmaker) as session:
        session.add(
            TriggerORM(
                id=uuid4(),
                tenant_id=tenant_id,
                flow_id=UUID(flow_id),
                kind=TriggerKind.SCHEDULE.value,
                schedule_cron="0 * * * *",
                event_type=None,
                active=active,
                created_at=datetime.now(UTC),
            )
        )


def _tenant_id() -> UUID:
    return UUID(get_settings().default_tenant_id)


async def test_list_shows_label_and_never_leaks_token(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            account_id = await _create_account(client, "tok-label")
            label_resp = await client.post(
                f"/accounts/{account_id}/label", json={"label": "Основной"}
            )
            assert label_resp.status_code == 200, label_resp.text

            list_resp = await client.get("/accounts/list")

    assert list_resp.status_code == 200, list_resp.text
    rows = list_resp.json()
    row = next(r for r in rows if r["id"] == account_id)
    assert row["label"] == "Основной"
    assert "encrypted_token" not in row
    assert "token_hash" not in row


async def test_delete_blocked_by_active_schedule_trigger_and_account_survives(
    sqlite_app: Any,
) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            account_id = await _create_account(client, "tok-blocked")
            flow_id = await _create_flow(
                client, _flow_spec("Автобамп", [_node("n1", account_ref=account_id)])
            )
            await _add_trigger(sqlite_app, _tenant_id(), flow_id, active=True)

            delete_resp = await client.post(f"/accounts/{account_id}/delete")
            list_resp = await client.get("/accounts/list")

    assert delete_resp.status_code == 409, delete_resp.text
    body = delete_resp.json()
    assert body["code"] == "ERR-1011"
    assert "Автобамп" in body["message"]
    assert account_id in {r["id"] for r in list_resp.json()}


async def test_delete_blocked_by_nested_children_reference(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            account_id = await _create_account(client, "tok-nested")
            nested = _node(
                "batch",
                children=[_node("batch_child", account_ref=account_id)],
            )
            flow_id = await _create_flow(client, _flow_spec("Переценка", [nested]))
            await _add_trigger(sqlite_app, _tenant_id(), flow_id, active=True)

            delete_resp = await client.post(f"/accounts/{account_id}/delete")
            list_resp = await client.get("/accounts/list")

    assert delete_resp.status_code == 409, delete_resp.text
    assert "Переценка" in delete_resp.json()["message"]
    assert account_id in {r["id"] for r in list_resp.json()}


async def test_delete_allowed_when_trigger_inactive(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            account_id = await _create_account(client, "tok-inactive-trigger")
            flow_id = await _create_flow(
                client, _flow_spec("Спящая задача", [_node("n1", account_ref=account_id)])
            )
            await _add_trigger(sqlite_app, _tenant_id(), flow_id, active=False)

            delete_resp = await client.post(f"/accounts/{account_id}/delete")
            list_resp = await client.get("/accounts/list")

    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json()["deleted"] is True
    assert account_id not in {r["id"] for r in list_resp.json()}


async def test_delete_succeeds_when_unreferenced(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            account_id = await _create_account(client, "tok-unreferenced")

            delete_resp = await client.post(f"/accounts/{account_id}/delete")
            list_resp = await client.get("/accounts/list")

    assert delete_resp.status_code == 200, delete_resp.text
    assert account_id not in {r["id"] for r in list_resp.json()}


async def test_duplicate_label_rejected_but_null_labels_coexist(sqlite_app: Any) -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await _create_account(client, "tok-dup-1")
            second = await _create_account(client, "tok-dup-2")
            # Both accounts still carry the NULL default label — must coexist.
            list_before = await client.get("/accounts/list")

            ok_resp = await client.post(f"/accounts/{first}/label", json={"label": "Общий"})
            conflict_resp = await client.post(f"/accounts/{second}/label", json={"label": "Общий"})
            # Explicitly re-nulling the second account's label must not collide either.
            renull_resp = await client.post(f"/accounts/{second}/label", json={"label": None})

    assert list_before.status_code == 200
    assert ok_resp.status_code == 200, ok_resp.text
    assert conflict_resp.status_code == 409, conflict_resp.text
    assert conflict_resp.json()["code"] == "ERR-1011"
    assert renull_resp.status_code == 200, renull_resp.text


def test_spec_references_recurses_into_children() -> None:
    account_id = AccountId(uuid4())
    other_id = AccountId(uuid4())
    spec = {
        "nodes": [
            {
                "id": "batch",
                "children": [
                    {"id": "batch-child", "account_ref": str(account_id)},
                ],
            }
        ]
    }

    assert _spec_references(spec, account_id) is True
    assert _spec_references(spec, other_id) is False
