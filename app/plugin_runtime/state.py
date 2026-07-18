"""PluginState — the two install toggles, in `<plugin_dir>/state.json`.

Not a DB row: two booleans that the bot flips and the update loop reads do not earn a migration.
Written atomically (temp + rename) so a concurrent read never sees a half-written file. Default is
both OFF — a fresh install neither auto-updates nor pings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import ValidationError

from app.core.schema import BaseSchema

_STATE_FILENAME: Final = "state.json"


class PluginToggles(BaseSchema):
    auto_update: bool = False
    alerts: bool = False


class PluginState:
    def __init__(self, plugin_dir: Path) -> None:
        self._path = plugin_dir / _STATE_FILENAME

    def read(self) -> PluginToggles:
        """The stored toggles, or the both-OFF default if the file is absent or unreadable."""
        try:
            return PluginToggles.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return PluginToggles()

    def write(self, toggles: PluginToggles) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        tmp.write_text(toggles.model_dump_json(), encoding="utf-8")
        tmp.replace(self._path)  # atomic on POSIX and Windows
