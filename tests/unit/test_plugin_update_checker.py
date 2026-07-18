"""compute_updates (pure), the notifier, and PluginUpdateChecker.tick over the toggles (T5)."""

from __future__ import annotations

from typing import Any

import pytest

from app.plugin_runtime.dtos import PluginCatalogView, PluginTogglesView, PluginUpdate
from app.plugin_runtime.notifier import format_updates
from app.plugin_runtime.texts import load_plugin_texts
from app.plugin_runtime.update_checker import PluginUpdateChecker, compute_updates

_CATALOG = {
    "available": [{"name": "demo", "version": "2.0.0", "description": "d"}],
    "installed": [{"name": "demo", "version": "1.0.0", "broken": False, "reason": None}],
}


def test_newer_available_is_an_update() -> None:
    updates = compute_updates({"demo": "2.0.0"}, {"demo": "1.0.0"})
    assert updates == [PluginUpdate(name="demo", current="1.0.0", available="2.0.0")]


def test_same_or_older_is_no_update() -> None:
    assert compute_updates({"demo": "1.0.0"}, {"demo": "1.0.0"}) == []
    assert compute_updates({"demo": "1.0.0"}, {"demo": "2.0.0"}) == []


def test_unparseable_or_absent_is_skipped() -> None:
    assert compute_updates({"demo": "not-a-version"}, {"demo": "1.0.0"}) == []
    assert compute_updates({}, {"demo": "1.0.0"}) == []


def test_format_updates_uses_configurable_texts() -> None:
    texts = load_plugin_texts()
    body = format_updates([PluginUpdate(name="demo", current="1.0.0", available="2.0.0")], texts)
    assert "demo" in body and "1.0.0" in body and "2.0.0" in body
    assert body.splitlines()[0] == texts.updates_header


class _FakeApi:
    def __init__(self, settings: dict[str, Any], catalog: dict[str, Any]) -> None:
        self._settings = PluginTogglesView.model_validate(settings)
        self._catalog = PluginCatalogView.model_validate(catalog)
        self.installed: list[str] = []

    async def get_plugin_settings(self) -> PluginTogglesView:
        return self._settings

    async def list_plugins(self) -> PluginCatalogView:
        return self._catalog

    async def install_plugin(self, name: str) -> PluginCatalogView:
        self.installed.append(name)
        return self._catalog


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _checker(
    settings: dict[str, Any], notifier: _FakeNotifier, api: _FakeApi
) -> PluginUpdateChecker:
    return PluginUpdateChecker(
        api=api,
        notifier=notifier,
        admin_ids=frozenset({7}),
        texts=load_plugin_texts(),
        interval_s=3600,
    )


@pytest.mark.asyncio
async def test_tick_both_off_is_noop() -> None:
    api = _FakeApi({"auto_update": False, "alerts": False}, _CATALOG)
    notifier = _FakeNotifier()
    await _checker({}, notifier, api).tick()
    assert api.installed == [] and notifier.sent == []


@pytest.mark.asyncio
async def test_tick_alerts_only_notifies() -> None:
    api = _FakeApi({"auto_update": False, "alerts": True}, _CATALOG)
    notifier = _FakeNotifier()
    await _checker({}, notifier, api).tick()
    assert api.installed == []
    assert [chat for chat, _ in notifier.sent] == [7]


@pytest.mark.asyncio
async def test_tick_auto_update_installs_no_alert() -> None:
    api = _FakeApi({"auto_update": True, "alerts": False}, _CATALOG)
    notifier = _FakeNotifier()
    await _checker({}, notifier, api).tick()
    assert api.installed == ["demo"]
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_tick_both_on_installs_and_notifies() -> None:
    api = _FakeApi({"auto_update": True, "alerts": True}, _CATALOG)
    notifier = _FakeNotifier()
    await _checker({}, notifier, api).tick()
    assert api.installed == ["demo"]
    assert len(notifier.sent) == 1
