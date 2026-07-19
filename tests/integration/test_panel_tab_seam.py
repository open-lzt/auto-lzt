"""The panel tab seam, driven through the real plugin-runtime lifecycle.

The assertion that makes this a seam rather than a decoration: built-in tabs travel the SAME path as
contributed ones. If the host had a privileged second route, every test here could pass while a
plugin's tab never rendered — which is exactly the failure a "supports plugins" claim hides.
"""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.core.config import Settings
from app.domain.panel.tabs import (
    BUILTIN_PANEL_TABS,
    DuplicatePanelTabKey,
    PanelTabSpec,
    build_panel_tabs,
    stamp_origin,
)
from app.main import create_app
from app.plugin_runtime.contracts import (
    DiscoveredPlugin,
    PluginLoadContext,
    PluginLoadedContext,
    PluginProcess,
    PluginSource,
)
from app.plugin_runtime.manager import PluginManager


def _plugin(name: str, *tabs: PanelTabSpec) -> DiscoveredPlugin:
    def pre_init(_ctx: PluginLoadContext) -> PluginLoadedContext:
        return PluginLoadedContext(panel_tabs=list(tabs))

    return DiscoveredPlugin(
        name=name, source=PluginSource.ENTRY_POINT, pre_init=(pre_init,), post_init=(), shutdown=()
    )


def _manager(process: PluginProcess, *plugins: DiscoveredPlugin) -> PluginManager:
    manager = PluginManager(process, Settings())
    manager._plugins = list(plugins)  # noqa: SLF001 — bypasses entry-point discovery only
    manager._discovered = True  # noqa: SLF001
    return manager


def test_a_contributed_tab_reaches_the_api_process() -> None:
    contributions = _manager(
        PluginProcess.API, _plugin("demo", PanelTabSpec(key="checker", title="Чекер", order=40))
    ).pre_init()

    assert [t.key for t in contributions.panel_tabs] == ["checker"]
    assert contributions.panel_tabs[0].origin == "demo", "origin is stamped by the manager"


@pytest.mark.parametrize("process", [PluginProcess.WORKER, PluginProcess.BOT])
def test_tabs_are_empty_outside_the_api_process(process: PluginProcess) -> None:
    """The API process is the one that serves /panel/tabs; a worker holding tab specs would be
    carrying state it can never use."""
    contributions = _manager(
        process, _plugin("demo", PanelTabSpec(key="checker", title="Чекер"))
    ).pre_init()

    assert contributions.panel_tabs == ()


def test_two_plugins_claiming_one_key_fail_startup_naming_both_sides() -> None:
    contributed = _manager(
        PluginProcess.API,
        _plugin("alpha", PanelTabSpec(key="reports", title="Отчёты")),
        _plugin("beta", PanelTabSpec(key="reports", title="Отчётики")),
    ).pre_init()

    with pytest.raises(DuplicatePanelTabKey) as caught:
        build_panel_tabs(extra_tabs=contributed.panel_tabs)

    assert caught.value.key == "reports"
    assert {caught.value.existing_origin, caught.value.incoming_origin} == {"alpha", "beta"}


def test_a_plugin_cannot_shadow_a_builtin_tab() -> None:
    """Built-ins are in the same claim space, which is what "the same path" buys."""
    contributed = _manager(
        PluginProcess.API, _plugin("squatter", PanelTabSpec(key="tasks", title="Не задачи"))
    ).pre_init()

    with pytest.raises(DuplicatePanelTabKey) as caught:
        build_panel_tabs(extra_tabs=contributed.panel_tabs)

    assert caught.value.key == "tasks"
    assert caught.value.existing_origin == "builtin"
    assert caught.value.incoming_origin == "squatter"


def test_builtins_come_through_the_same_assembly_as_plugin_tabs() -> None:
    assembled = build_panel_tabs()
    assert {t.key for t in assembled} == {t.key for t in BUILTIN_PANEL_TABS}
    assert all(t.origin == "builtin" for t in assembled)


def test_tabs_are_ordered_by_declared_order_not_load_order() -> None:
    """A plugin picks its position by declaring a number. Otherwise the tab strip reshuffles
    whenever pip resolves packages in a different order."""
    contributed = stamp_origin([PanelTabSpec(key="first", title="Первая", order=1)], "early")
    assembled = build_panel_tabs(extra_tabs=tuple(contributed))

    assert assembled[0].key == "first"
    assert [t.order for t in assembled] == sorted(t.order for t in assembled)


async def test_the_endpoint_serves_the_assembled_set() -> None:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/panel/tabs")

    assert resp.status_code == 200
    body = resp.json()
    assert [t["key"] for t in body] == [t.key for t in build_panel_tabs()]
    assert body[0]["title"] == "Задачи"
    assert all(t["origin"] for t in body), "origin tells an operator which package added a tab"


def test_the_filter_helper_keeps_every_surface_consistent() -> None:
    """All four contribution fields go through one keep-or-drop helper, so a fifth cannot quietly
    reintroduce a hand-written ternary that disagrees with the others."""
    api = _manager(PluginProcess.API, _plugin("demo", PanelTabSpec(key="x", title="X"))).pre_init()
    worker = _manager(
        PluginProcess.WORKER, _plugin("demo", PanelTabSpec(key="x", title="X"))
    ).pre_init()

    assert isinstance(api.panel_tabs, tuple)
    assert isinstance(worker.panel_tabs, tuple)
    assert api.api_routers == () and worker.api_routers == ()
    assert worker.panel_tabs == ()
