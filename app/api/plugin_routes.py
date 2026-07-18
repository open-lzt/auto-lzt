"""Plugin routes — the owner-only surface the bot drives to install/remove plugins and flip the two
update toggles. Every route takes the API key: installing runs owner code, and even the catalog is
an operator surface (it reveals which git repo this stand trusts). The bot never writes files or
shells pip itself — it calls here, and the install service (infra) does the work (D-5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.auth import protect
from app.core.schema import BaseSchema
from app.plugin_runtime.dtos import (
    AvailablePlugin,
    InstalledPluginView,
    PluginCatalogView,
    PluginTogglesView,
)
from app.plugin_runtime.install_service import PluginInstallService
from app.plugin_runtime.state import PluginState, PluginToggles

router = APIRouter(prefix="/plugins", tags=["plugins"], dependencies=protect())


class PluginNameRequest(BaseSchema):
    name: str


def _service(request: Request) -> PluginInstallService:
    svc: PluginInstallService = request.app.state.plugin_install_service
    return svc


def _state(request: Request) -> PluginState:
    state: PluginState = request.app.state.plugin_state
    return state


async def _catalog(svc: PluginInstallService) -> PluginCatalogView:
    return PluginCatalogView(
        available=[
            AvailablePlugin(name=e.name, version=e.version, description=e.description)
            for e in await svc.available()
        ],
        installed=[
            InstalledPluginView(name=p.name, version=p.version, broken=p.broken, reason=p.reason)
            for p in svc.installed()
        ],
    )


@router.get("/catalog")
async def catalog(svc: PluginInstallService = Depends(_service)) -> PluginCatalogView:
    """Available (git catalog, `[]` when unreachable) plus installed (folder, with broken flags)."""
    return await _catalog(svc)


@router.post("/install")
async def install(
    body: PluginNameRequest, svc: PluginInstallService = Depends(_service)
) -> PluginCatalogView:
    """Install (or update) a catalog plugin into the folder. Applies on next restart. Returns the
    fresh catalog so the bot re-renders."""
    await svc.install(body.name)
    return await _catalog(svc)


@router.post("/remove")
async def remove(
    body: PluginNameRequest, svc: PluginInstallService = Depends(_service)
) -> PluginCatalogView:
    svc.remove(body.name)
    return await _catalog(svc)


@router.get("/settings")
async def get_settings(state: PluginState = Depends(_state)) -> PluginTogglesView:
    toggles = state.read()
    return PluginTogglesView(auto_update=toggles.auto_update, alerts=toggles.alerts)


@router.put("/settings")
async def put_settings(
    body: PluginTogglesView, state: PluginState = Depends(_state)
) -> PluginTogglesView:
    state.write(PluginToggles(auto_update=body.auto_update, alerts=body.alerts))
    return body
