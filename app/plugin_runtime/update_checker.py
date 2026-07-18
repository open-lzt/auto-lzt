"""Plugin update checking — a `Worker` subclass that runs in the bot process (F9: only the bot holds
the Bot and the admin ids).

`compute_updates` is pure (version math). `PluginUpdateChecker` is the worker: each tick reads
the toggles (both OFF → no-op), fetches it, and on newer versions either re-installs via the
API (`auto_update`) or DMs the admins (`alerts`) — through the `Notifier` port, so the runtime never
talks to Telegram directly. Everything crosses the boundary as a typed DTO, never a raw dict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

import structlog
from packaging.version import InvalidVersion, Version

from app.plugin_runtime.dtos import PluginCatalogView, PluginTogglesView, PluginUpdate
from app.plugin_runtime.notifier import Notifier, notify_updates
from app.plugin_runtime.texts import PluginTexts
from app.plugin_runtime.worker import Worker

log = structlog.get_logger()


def _is_newer(available: str, current: str) -> bool:
    try:
        return Version(available) > Version(current)
    except InvalidVersion:
        return False


def compute_updates(
    available: Mapping[str, str], installed: Mapping[str, str]
) -> list[PluginUpdate]:
    """Every installed plugin whose catalog version is strictly newer than its installed one."""
    return [
        PluginUpdate(name=name, current=current, available=available[name])
        for name, current in installed.items()
        if name in available and _is_newer(available[name], current)
    ]


class PluginApi(Protocol):
    """What the checker needs from the API — FlowApiClient satisfies it structurally, so the runtime
    does not import the bot's transport."""

    async def get_plugin_settings(self) -> PluginTogglesView: ...
    async def list_plugins(self) -> PluginCatalogView: ...
    async def install_plugin(self, name: str) -> PluginCatalogView: ...


class PluginUpdateChecker(Worker):
    def __init__(
        self,
        *,
        api: PluginApi,
        notifier: Notifier,
        admin_ids: frozenset[int],
        texts: PluginTexts,
        interval_s: int,
    ) -> None:
        super().__init__(name="plugin-update-checker", interval_s=interval_s)
        self._api = api
        self._notifier = notifier
        self._admin_ids = admin_ids
        self._texts = texts

    async def tick(self) -> None:
        toggles = await self._api.get_plugin_settings()
        if not (toggles.auto_update or toggles.alerts):
            return
        catalog = await self._api.list_plugins()
        updates = compute_updates(
            {p.name: p.version for p in catalog.available},
            {p.name: p.version for p in catalog.installed},
        )
        if not updates:
            return
        if toggles.auto_update:
            await self._apply(updates)
        if toggles.alerts:
            await notify_updates(self._notifier, self._admin_ids, updates, self._texts)

    async def _apply(self, updates: Sequence[PluginUpdate]) -> None:
        for update in updates:
            try:
                await self._api.install_plugin(update.name)
            except Exception as exc:  # noqa: BLE001 — one failed install must not stop the rest
                log.error("plugin.auto_update_failed", plugin=update.name, error=repr(exc))
