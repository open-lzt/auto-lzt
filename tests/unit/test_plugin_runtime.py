"""PluginManager unit tests — driven over a FAKE entry-point set (no installed distribution).

The fixture doubles are the entry points and the plugin modules; everything else is the real
manager. Routers are plain sentinels here — the manager only routes them by process, it never
inspects them, so a unit test need not import fastapi/aiogram.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import (
    NodeCategory,
    NodeRegistration,
    NodeRegistry,
    NodeType,
)
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.plugin_runtime import (
    PluginLoadContext,
    PluginLoadedContext,
    PluginManager,
    PluginProcess,
)
from app.plugin_runtime.errors import PluginHookError, PluginLoadError


class _In(BaseSchema):
    pass


class _Out(BaseSchema):
    ok: bool


class _DummyNode(BaseNode):
    node_type = "test.dummy"

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        return StepResultDTO(node_id=ctx.node.id, output={"ok": True})


def _reg(key: str = "test.dummy") -> NodeRegistration:
    return NodeRegistration(
        node_type=NodeType(
            key=key,
            category=NodeCategory.LOGIC,
            input_schema=_In,
            output_schema=_Out,
            idempotent=True,
            capabilities=frozenset({NodeCapability.PURE}),
        ),
        impl=_DummyNode,
    )


class _FakeEP:
    def __init__(self, name: str, module: object) -> None:
        self.name = name
        self._module = module

    def load(self) -> object:
        return self._module


class _BoomEP:
    name = "boom"

    def load(self) -> object:
        raise RuntimeError("import failed")


def _mgr(
    monkeypatch: pytest.MonkeyPatch, eps: list[object], process: PluginProcess
) -> PluginManager:
    monkeypatch.setattr("app.plugin_runtime.manager.entry_points", lambda group: eps)
    return PluginManager(process, get_settings())


def test_discover_reads_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pre(ctx: PluginLoadContext) -> PluginLoadedContext:
        return PluginLoadedContext()

    module = SimpleNamespace(PRE_INIT=[_pre], POST_INIT=[], SHUTDOWN=[])
    mgr = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.WORKER)
    mgr.discover()
    mgr.discover()  # idempotent — no double-registration
    contributions = mgr.pre_init()
    assert contributions.nodes == ()


def test_discover_fails_closed_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _mgr(monkeypatch, [_BoomEP()], PluginProcess.WORKER)
    with pytest.raises(PluginLoadError) as exc:
        mgr.discover()
    assert exc.value.plugin_name == "boom"


def test_discover_fails_closed_on_non_callable_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace(PRE_INIT=["not-callable"], POST_INIT=[], SHUTDOWN=[])
    mgr = _mgr(monkeypatch, [_FakeEP("bad", module)], PluginProcess.WORKER)
    with pytest.raises(PluginLoadError):
        mgr.discover()


def test_pre_init_merges_and_stamps_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pre_a(ctx: PluginLoadContext) -> PluginLoadedContext:
        loaded = PluginLoadedContext()
        loaded.nodes.append(_reg("test.a"))
        return loaded

    def _pre_b(ctx: PluginLoadContext) -> PluginLoadedContext:
        loaded = PluginLoadedContext()
        loaded.nodes.append(_reg("test.b"))
        return loaded

    eps = [
        _FakeEP("alpha", SimpleNamespace(PRE_INIT=[_pre_a], POST_INIT=[], SHUTDOWN=[])),
        _FakeEP("beta", SimpleNamespace(PRE_INIT=[_pre_b], POST_INIT=[], SHUTDOWN=[])),
    ]
    mgr = _mgr(monkeypatch, eps, PluginProcess.API)
    mgr.discover()
    contributions = mgr.pre_init()
    origins = {reg.node_type.key: reg.origin for reg in contributions.nodes}
    assert origins == {"test.a": "alpha", "test.b": "beta"}


def test_per_process_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pre(ctx: PluginLoadContext) -> PluginLoadedContext:
        loaded = PluginLoadedContext()
        loaded.nodes.append(_reg())
        loaded.api_routers.append(object())  # sentinel — filter doesn't inspect it
        loaded.bot_routers.append(object())
        return loaded

    module = SimpleNamespace(PRE_INIT=[_pre], POST_INIT=[], SHUTDOWN=[])

    api = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.API)
    api.discover()
    api_contrib = api.pre_init()
    assert len(api_contrib.nodes) == 1
    assert len(api_contrib.api_routers) == 1
    assert api_contrib.bot_routers == ()

    worker = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.WORKER)
    worker.discover()
    worker_contrib = worker.pre_init()
    assert len(worker_contrib.nodes) == 1
    assert worker_contrib.api_routers == ()
    assert worker_contrib.bot_routers == ()

    bot = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.BOT)
    bot.discover()
    bot_contrib = bot.pre_init()
    assert bot_contrib.nodes == ()
    assert len(bot_contrib.bot_routers) == 1


def test_pre_init_bad_return_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _pre(ctx: PluginLoadContext) -> PluginLoadedContext:
        return "not a context"  # type: ignore[return-value]

    module = SimpleNamespace(PRE_INIT=[_pre], POST_INIT=[], SHUTDOWN=[])
    mgr = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.API)
    mgr.discover()
    with pytest.raises(PluginHookError) as exc:
        mgr.pre_init()
    assert exc.value.phase == "pre_init"


@pytest.mark.asyncio
async def test_spawn_task_cancelled_before_shutdown_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    started = asyncio.Event()

    async def _loop() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("task_cancelled")
            raise

    async def _start(ctx: object) -> None:
        ctx.spawn(_loop(), "loop")  # type: ignore[attr-defined]

    async def _stop(ctx: object) -> None:
        events.append("shutdown_hook")

    module = SimpleNamespace(PRE_INIT=[], POST_INIT=[_start], SHUTDOWN=[_stop])
    mgr = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.WORKER)
    mgr.discover()
    mgr.pre_init()
    await mgr.post_init(node_registry=NodeRegistry([]))
    await started.wait()
    await mgr.shutdown()
    assert events == ["task_cancelled", "shutdown_hook"]


@pytest.mark.asyncio
async def test_shutdown_hook_raise_is_logged_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def _boom(ctx: object) -> None:
        raise RuntimeError("cleanup failed")

    async def _rec(ctx: object) -> None:
        events.append("second")

    module = SimpleNamespace(PRE_INIT=[], POST_INIT=[], SHUTDOWN=[_boom, _rec])
    mgr = _mgr(monkeypatch, [_FakeEP("p", module)], PluginProcess.WORKER)
    mgr.discover()
    mgr.pre_init()
    await mgr.post_init(node_registry=NodeRegistry([]))
    await mgr.shutdown()  # must not raise
    assert events == ["second"]
