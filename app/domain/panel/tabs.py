"""Panel tabs — the surface a plugin contributes to the browser UI.

WHY THIS EXISTS. A plugin platform already ships here: entry points under ``lzt_flow.plugins``,
lifecycle hooks, per-process contribution filtering, bot-side install. A panel that is the ONE
surface a plugin cannot reach would be an inconsistency inside the product, and the first question a
reader of ``docs/plugins.md`` would ask. This closes it by adding one field to a shipped mechanism
rather than inventing a second mechanism.

WHAT A PLUGIN CAN AND CANNOT DO, stated plainly rather than glossed: it can contribute a backend
surface and a tab, but NOT its own UI bundle. The frontend resolves a tab key to a feature module it
already knows. Shipping plugin-supplied UI needs either a declarative card/settings DSL or Module
Federation, and neither is built — naming that boundary precisely is the honest engineering signal;
a speculative DSL would not be.

Built-ins go through the SAME assembly as plugin tabs (``build_panel_tabs``), mirroring how
``build_registry`` handles built-in nodes. A privileged second path for the host's own tabs would
make the seam decorative — it would work for us and be untested for everyone else.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final


@dataclass(slots=True, frozen=True)
class PanelTabSpec:
    """One tab. ``key`` is the contract with the frontend, which maps it to a feature module."""

    key: str
    title: str
    order: int = 100
    icon: str | None = None
    origin: str = "builtin"


class DuplicatePanelTabKey(Exception):
    """Two contributions claim one tab key. Raised at startup, never per request — a process whose
    panel is ambiguous must not serve traffic, because WHICH tab a key resolves to would then depend
    on plugin load order. Names both sides: "duplicate key" without the two origins sends the
    operator hunting through every installed plugin."""

    def __init__(self, key: str, existing_origin: str, incoming_origin: str) -> None:
        super().__init__(
            f"panel tab {key!r} claimed by both {existing_origin!r} and {incoming_origin!r}"
        )
        self.key = key
        self.existing_origin = existing_origin
        self.incoming_origin = incoming_origin


# Every ``icon`` here must name a symbol that actually exists in @open-lzt/ui's sprite
# (``window.lztIcons`` lists them). Three of these used to be `workflow`/`history`/`blocks`,
# which the sprite has never shipped — `<use href="#i-workflow">` renders nothing at all, so
# those tabs were silently icon-less. Prefer an existing symbol over inventing a name: the
# sprite lives in a separate package, and the panel consumes its BUILT copy.
BUILTIN_PANEL_TABS: Final[tuple[PanelTabSpec, ...]] = (
    PanelTabSpec(key="tasks", title="Задачи", order=10, icon="clock"),
    PanelTabSpec(key="autobump", title="Поднятие", order=20, icon="zap"),
    PanelTabSpec(key="threadbump", title="Поднятие тем", order=25, icon="message"),
    PanelTabSpec(key="autobuy", title="Автобай", order=28, icon="wallet"),
    PanelTabSpec(key="accounts", title="Аккаунты", order=30, icon="user"),
    PanelTabSpec(key="flows", title="Флоу", order=40, icon="share"),
    PanelTabSpec(key="registry", title="Реестр", order=45, icon="package"),
    PanelTabSpec(key="history", title="История", order=50, icon="list"),
    # Authoring-only. The backend advertises it because the capability exists server-side; a build
    # with the builder switched off filters it out client-side rather than showing it broken.
    PanelTabSpec(key="composites", title="Составные блоки", order=60, icon="grid"),
)


def build_panel_tabs(extra_tabs: tuple[PanelTabSpec, ...] = ()) -> tuple[PanelTabSpec, ...]:
    """Assemble the process's tab set, ordered, failing closed on a key collision.

    Sorted by ``(order, key)`` so a plugin picks its position by declaring a number rather than by
    getting lucky with load order — and so the result is deterministic across restarts, which is
    what makes the rendered tab strip stable.
    """
    claimed: dict[str, str] = {}
    tabs: list[PanelTabSpec] = []
    for tab in (*BUILTIN_PANEL_TABS, *extra_tabs):
        existing = claimed.get(tab.key)
        if existing is not None:
            raise DuplicatePanelTabKey(tab.key, existing, tab.origin)
        claimed[tab.key] = tab.origin
        tabs.append(tab)
    return tuple(sorted(tabs, key=lambda t: (t.order, t.key)))


def stamp_origin(tabs: list[PanelTabSpec], origin: str) -> list[PanelTabSpec]:
    """Attribute each tab to the plugin that contributed it. Stamped by the manager, not
    self-declared, for the same reason node origins are: a plugin naming someone else as the origin
    of its own tab would make the collision error point at the wrong package."""
    return [replace(tab, origin=origin) for tab in tabs]
