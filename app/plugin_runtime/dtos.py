"""Typed views of the plugin catalog + toggles — the DTOs that cross the bot⇄API boundary.

The API returns these; the bot parses its JSON into the SAME models (`model_validate`) instead of
poking at raw dicts, so the update checker and the menu are typed end to end.
"""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema


class AvailablePlugin(BaseSchema):
    name: str
    version: str
    description: str = ""


class InstalledPluginView(BaseSchema):
    name: str
    version: str
    broken: bool = False
    reason: str | None = None


class PluginCatalogView(BaseSchema):
    available: list[AvailablePlugin] = Field(default_factory=list)
    installed: list[InstalledPluginView] = Field(default_factory=list)


class PluginTogglesView(BaseSchema):
    auto_update: bool = False
    alerts: bool = False


class PluginUpdate(BaseSchema):
    name: str
    current: str
    available: str
