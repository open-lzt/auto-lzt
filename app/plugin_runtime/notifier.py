"""Update notifications — a transport-agnostic port + one function that formats and sends.

`Notifier` is the seam: the runtime does not know it is Telegram (the bot supplies an adapter).
`notify_updates` builds the message from the configurable `PluginTexts` and DMs each admin; a failed
DM is logged, never fatal.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

import structlog

from app.plugin_runtime.dtos import PluginUpdate
from app.plugin_runtime.texts import PluginTexts

log = structlog.get_logger()


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str) -> None: ...


def format_updates(updates: Sequence[PluginUpdate], texts: PluginTexts) -> str:
    lines = [
        texts.update_line.format(name=u.name, current=u.current, available=u.available)
        for u in updates
    ]
    return "\n".join([texts.updates_header, *lines])


async def notify_updates(
    notifier: Notifier,
    admin_ids: Iterable[int],
    updates: Sequence[PluginUpdate],
    texts: PluginTexts,
) -> None:
    if not updates:
        return
    text = format_updates(updates, texts)
    for admin_id in admin_ids:
        try:
            await notifier.send(admin_id, text)
        except Exception as exc:  # noqa: BLE001 — a failed DM must not stop the rest
            log.warning("plugin.alert_failed", admin=admin_id, error=repr(exc))
