"""GET /panel/tabs — the assembled tab strip the panel shell renders.

Built-in and plugin-contributed tabs arrive through one list, assembled once in the lifespan. The
shell renders whatever it is given, in the given order, so adding a tab is a backend fact rather
than a frontend edit.

The read is open, matching the catalog rather than the run reads: a tab key and a label name a
capability this installation has, not the operator's data. The endpoints BEHIND a tab carry their
own auth.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.schema import BaseSchema

router = APIRouter(prefix="/panel", tags=["panel"])


class PanelTabDTO(BaseSchema):
    key: str
    title: str
    order: int
    icon: str | None
    origin: str


@router.get("/tabs")
async def list_panel_tabs(request: Request) -> list[PanelTabDTO]:
    """``origin`` ships deliberately: when a plugin adds a tab, an operator looking at the panel
    should be able to tell which package put it there without reading the plugin list."""
    return [
        PanelTabDTO(key=t.key, title=t.title, order=t.order, icon=t.icon, origin=t.origin)
        for t in request.app.state.panel_tabs
    ]
