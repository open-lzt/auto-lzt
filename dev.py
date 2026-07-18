"""Single-file, no-Docker dev runner for lzt-flow.

Runs the whole app locally with ZERO external services — SQLite (async) instead of Postgres,
in-process fakeredis instead of Redis, and (by default) an in-process mock of the lzt.market API
so a flow actually completes without a live token. It also runs an in-process run executor
(the real `execute_run` runtime; arq's queue is bypassed as transport only), so `POST /runs`
truly runs the flow — no separate worker process.

Usage:
    uv run python dev.py                # serve API on http://127.0.0.1:8000 (mock market)
    uv run python dev.py --demo         # boot, drive one bump flow end-to-end, print result, exit
    uv run python dev.py --no-mock      # hit the REAL api.lzt.market (needs a real token)
    uv run python dev.py --token <tok>  # seed the dev account with a real market token

Dev-only glue; it imports the real app unchanged. Nothing in `app/` depends on it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import traceback
from datetime import UTC, datetime
from uuid import UUID, uuid4

os.environ.setdefault("LZT_FLOW_DATABASE_URL", "sqlite+aiosqlite:///dev.db")
os.environ.setdefault("LZT_FLOW_REDIS_URL", "redis://dev-fake/0")
os.environ.setdefault("LZT_FLOW_MASTER_KEY", "dev-only-master-key-not-secret")
os.environ.setdefault("LZT_FLOW_ALLOW_UNAUTHENTICATED", "1")

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import redis.asyncio as _redis_asyncio  # noqa: E402

_FAKE_SERVER = fakeredis.FakeServer()


def _fake_from_url(*_args: object, **kwargs: object) -> fakeredis.aioredis.FakeRedis:
    decode = bool(kwargs.get("decode_responses", False))
    return fakeredis.aioredis.FakeRedis(server=_FAKE_SERVER, decode_responses=decode)


_redis_asyncio.from_url = _fake_from_url  # type: ignore[assignment]

import uvicorn  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.base import make_engine, make_sessionmaker, session_scope  # noqa: E402
from app.db.models import AccountORM, Base, RunORM  # noqa: E402
from app.domain.account.crypto import EnvelopeCipher  # noqa: E402
from app.domain.account.model import AccountStatus  # noqa: E402
from app.domain.catalog.plugins import build_registry  # noqa: E402
from app.domain.flow_engine.model import RunId, RunStatus  # noqa: E402
from app.domain.flow_engine.repo import (  # noqa: E402
    FlowIrRepository,
    RunRepository,
    RunStepRepository,
    RunTraceRepository,
)
from app.worker.arq_settings import _build_node_deps  # noqa: E402
from app.worker.runtime import execute_run  # noqa: E402

# ``NODE_REGISTRY`` used to be a module global in the removed ``app.worker.registry``. execute_run
# takes the node-class map (not a NodeRegistry), so build the built-in set the way the fixtures do.
NODE_REGISTRY = build_registry(load_plugins=False).node_classes()

DEV_TENANT = UUID("00000000-0000-0000-0000-000000000001")


async def _init_schema() -> None:
    """Create all tables on the dev SQLite file (no Alembic needed for throwaway dev)."""
    engine = make_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _seed_account(token: str) -> None:
    """Seed one ACTIVE dev account so the tenant token-pool has a credential."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    cipher = EnvelopeCipher(master_key=settings.master_key)
    async with session_scope(sm) as session:
        exists = (await session.execute(select(AccountORM).limit(1))).scalar_one_or_none()
        if exists is None:
            session.add(
                AccountORM(
                    id=uuid4(),
                    tenant_id=DEV_TENANT,
                    encrypted_token=cipher.encrypt(token, DEV_TENANT),  # type: ignore[arg-type]
                    created_at=datetime.now(UTC),
                    status=AccountStatus.ACTIVE.value,
                )
            )
    await engine.dispose()


async def _dev_executor(stop: asyncio.Event) -> None:
    """In-process run executor: poll for pending runs and drive the REAL runtime on each.

    This replaces the arq worker process for dev — arq is only the transport; `execute_run` is the
    real logic. The optimistic lock makes re-picking a run safe.
    """
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    cipher = EnvelopeCipher(master_key=settings.master_key)
    from app.domain.account.exclusion import AccountExcluder
    from app.domain.account.pool import TokenPool

    pool = TokenPool(sm, cipher, settings.market_base_url)
    excluder = AccountExcluder(sm, pool)
    redis = _fake_from_url(decode_responses=True)
    node_deps = _build_node_deps(
        sm, cipher, pool, excluder, redis, settings.market_base_url, settings
    )
    try:
        while not stop.is_set():
            async with session_scope(sm) as session:
                pending = (
                    (
                        await session.execute(
                            select(RunORM.id).where(
                                RunORM.status.in_(
                                    [RunStatus.PENDING.value, RunStatus.RUNNING.value]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for run_id in pending:
                try:
                    await execute_run(
                        RunId(run_id),
                        runs=RunRepository(sm),
                        steps=RunStepRepository(sm),
                        flows=FlowIrRepository(sm),
                        registry=NODE_REGISTRY,
                        node_deps=node_deps,
                        worker_id="dev-executor",
                        trace_sink=RunTraceRepository(sm),
                    )
                except Exception:  # noqa: BLE001 — dev loop must survive one bad run, but a
                    # swallowed failure here left runs stuck in `pending` with no clue why.
                    traceback.print_exc()
            await asyncio.sleep(0.4)
    finally:
        await redis.aclose()
        await engine.dispose()


def _install_arq_noop() -> None:
    """Make the run enqueue a no-op — the Run row is written to the DB before the enqueue, and the
    in-process dev executor picks it up (no real Redis needed). Patches the arq-pool factory the
    lifespan owns (app.main.create_pool), so app.state.arq_pool is the no-op."""
    import app.main as app_main

    class _NoOpPool:
        async def enqueue_job(self, *_a: object, **_k: object) -> None:
            return None

        async def aclose(self) -> None:
            return None

    async def _fake_create_pool(*_a: object, **_k: object) -> _NoOpPool:
        return _NoOpPool()

    app_main.create_pool = _fake_create_pool  # type: ignore[assignment]


@contextlib.contextmanager
def _maybe_mock_market(enabled: bool):  # type: ignore[no-untyped-def]
    """Intercept lzt.market/lolz.live with a canned 200 so a bump completes without a live token."""
    if not enabled:
        yield
        return
    import respx
    from httpx import Response

    with respx.mock(assert_all_called=False) as router:
        router.route(host="prod-api.lzt.market").mock(
            return_value=Response(200, json={"status": "ok", "message": "done"})
        )
        router.route(host="prod-api.lolz.live").mock(
            return_value=Response(200, json={"status": "ok", "message": "done"})
        )
        yield


async def _run_demo() -> None:
    """Drive one single-bump flow through the API and print the resulting run status."""
    import httpx
    from asgi_lifespan import LifespanManager

    from app.main import create_app

    app = create_app()
    stop = asyncio.Event()
    executor = asyncio.create_task(_dev_executor(stop))
    flow_json = {
        "name": "dev-bump",
        "nodes": [
            {
                "id": "b1",
                "type": "market.bump",
                "inputs": {"item_id": {"literal": 12345}},
                "account_ref": None,
                "edges": {},
            }
        ],
        "entry_node_id": "b1",
    }
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://dev") as c:
            flow = (await c.post("/flows/create", json=flow_json)).json()
            print("POST /flows/create ->", flow)
            flow_id = flow["flow_id"]
            compiled = (await c.post(f"/flows/{flow_id}/compile")).json()
            print("POST /compile ->", compiled)
            run = (await c.post("/runs/create", json={"flow_id": flow_id})).json()
            print("POST /runs/create ->", run)
            run_id = run["run_id"]
            status: dict[str, object] = run
            for _ in range(25):
                await asyncio.sleep(0.4)
                status = (await c.get(f"/runs/{run_id}/get")).json()
                if status.get("status") in {"completed", "failed"}:
                    break
            print("FINAL run ->", status)
    stop.set()
    await executor


async def _serve(port: int) -> None:
    from app.main import create_app

    app = create_app()
    stop = asyncio.Event()
    executor = asyncio.create_task(_dev_executor(stop))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        stop.set()
        await executor


def main() -> None:
    parser = argparse.ArgumentParser(description="lzt-flow no-Docker dev runner")
    parser.add_argument("--demo", action="store_true", help="drive one bump flow and exit")
    parser.add_argument("--no-mock", action="store_true", help="hit the real api.lzt.market")
    parser.add_argument("--token", default="dev-dummy-token", help="market token to seed")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LZT_FLOW_DEV_PORT", "8000")),
        help="TCP port for the dev server (env LZT_FLOW_DEV_PORT, default 8000)",
    )
    args = parser.parse_args()

    _install_arq_noop()

    settings = get_settings()
    if settings.market_base_url:
        print(
            f"lzt-flow dev server targeting TESTNET/SANDBOX market backend: "
            f"{settings.market_base_url}"
        )

    async def _boot() -> None:
        await _init_schema()
        await _seed_account(args.token)
        if args.demo:
            await _run_demo()
        else:
            print(
                f"lzt-flow dev server on http://127.0.0.1:{args.port}  (SQLite + fakeredis"
                + (", mock market)" if not args.no_mock else ", REAL market)")
            )
            await _serve(args.port)

    with _maybe_mock_market(enabled=not args.no_mock):
        asyncio.run(_boot())


if __name__ == "__main__":
    main()
