"""Frozen contract for the plugin runtime — the single source of truth the manager implements.

``from __future__ import annotations`` is load-bearing here: it turns every field annotation into a
string, so the ``TYPE_CHECKING``-only ``APIRouter`` / aiogram ``Router`` imports below are never
evaluated at runtime. That is what keeps ``contracts.py`` (and ``manager.py``, which imports it)
from pulling fastapi into the worker process or aiogram into the API process. The lists hold real
router instances at runtime — the plugin imports the transport itself and appends them; this module
only *names* the types in annotations. Do NOT call ``get_type_hints()`` on these dataclasses (it
would force the guarded imports); plain dataclass construction does not.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.stdlib import BoundLogger

from app.core.config import Settings
from app.domain.catalog.registry import NodeRegistration, NodeRegistry
from app.domain.panel.tabs import PanelTabSpec

if TYPE_CHECKING:
    from aiogram import Router as BotRouter
    from fastapi import APIRouter


class PluginProcess(StrEnum):
    """Which of the three long-lived processes is loading the plugin. Decides which contributions
    the manager applies: API → api_routers + nodes; WORKER → nodes; BOT → bot_routers."""

    API = "api"
    WORKER = "worker"
    BOT = "bot"


class PluginSource(StrEnum):
    """How a plugin was discovered. Governs the REACTION to a failed gate — one gate, two answers:
    an ENTRY_POINT plugin (deliberate `pip install`) fails the boot closed on a node-key collision
    or a broken hook; a FOLDER plugin (from the bot) is quarantined instead, so a bad install can
    never brick the boot the admin needs in order to remove it (D-4)."""

    ENTRY_POINT = "entry_point"
    FOLDER = "folder"


@dataclass(slots=True, frozen=True)
class PluginSettings:
    """The configuration a plugin is HANDED — a named projection of ``Settings``, not ``Settings``.

    The trap this avoids is not privilege: a plugin runs in-process and can import ``get_settings``
    itself, so this is not a sandbox and must not be described as one. It is about what the runtime
    *offers*. The full object carries ``master_key``, ``api_key``, ``database_url`` and the catalog
    PAT, and handing it over makes every one of those a documented part of the plugin contract —
    logged by any plugin that logs its context, and unremovable once a plugin depends on it.

    A field belongs here when a plugin has a reason to read it. Adding one is a deliberate act.
    """

    plugin_dir: Path
    default_tenant_id: str
    worker_id: str
    market_base_url: str | None
    egress_allowed_hosts: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> PluginSettings:
        return cls(
            plugin_dir=settings.plugin_dir,
            default_tenant_id=settings.default_tenant_id,
            worker_id=settings.worker_id,
            market_base_url=settings.market_base_url,
            egress_allowed_hosts=settings.egress_allowed_hosts,
        )


PRE_INIT_ATTR: Final = "PRE_INIT"  # list[PreInitHook]
POST_INIT_ATTR: Final = "POST_INIT"  # list[PostInitHook]
SHUTDOWN_ATTR: Final = "SHUTDOWN"  # list[ShutdownHook]
ENTRY_POINT_GROUP: Final = "lzt_flow.plugins"


@dataclass(slots=True, frozen=True)
class PluginLoadContext:
    """Input to a PRE_INIT hook (sync). Read-only handles available at discovery time."""

    process: PluginProcess  # the process running discovery — for plugins that branch on it
    plugin_name: str  # the entry-point name (== origin stamped onto its nodes)
    settings: PluginSettings
    logger: BoundLogger  # already bound with plugin=plugin_name


@dataclass(slots=True)
class PluginLoadedContext:
    """What one PRE_INIT hook contributes. The hook appends unconditionally; the manager applies
    only the surfaces the current process consumes. Node origin is stamped by the manager, not
    here."""

    nodes: list[NodeRegistration] = field(default_factory=list)
    api_routers: list[APIRouter] = field(default_factory=list)
    bot_routers: list[BotRouter] = field(default_factory=list)
    panel_tabs: list[PanelTabSpec] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class PluginContributions:
    """Aggregate of every plugin's PRE_INIT output for one process, already process-filtered:
    e.g. in WORKER, api_routers/bot_routers are empty."""

    nodes: tuple[NodeRegistration, ...]
    api_routers: tuple[APIRouter, ...]
    bot_routers: tuple[BotRouter, ...]
    panel_tabs: tuple[PanelTabSpec, ...]


# spawn(coro, name) — the manager creates the task, tracks it, and cancels it at SHUTDOWN before
# the SHUTDOWN hooks run. A spawned task that raises is logged, never silently swallowed.
SpawnFn = Callable[[Coroutine[Any, Any, None], str], None]


@dataclass(slots=True, frozen=True)
class PluginReadyContext:
    """Input to POST_INIT / SHUTDOWN (async). Live process context. ``redis`` / ``sessionmaker``
    are Optional because the bot process has neither DB nor redis (it is an API client).
    ``node_registry`` is always present — it is built by the time POST_INIT runs."""

    process: PluginProcess
    plugin_name: str
    settings: PluginSettings
    logger: BoundLogger
    node_registry: NodeRegistry
    redis: Redis | None  # API/WORKER only; None in BOT
    sessionmaker: async_sessionmaker[AsyncSession] | None  # API/WORKER only; None in BOT
    spawn: SpawnFn


PreInitHook = Callable[[PluginLoadContext], PluginLoadedContext]
PostInitHook = Callable[[PluginReadyContext], Awaitable[None]]
ShutdownHook = Callable[[PluginReadyContext], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class DiscoveredPlugin:
    name: str  # entry-point name / folder name → origin
    source: PluginSource
    pre_init: tuple[PreInitHook, ...]
    post_init: tuple[PostInitHook, ...]
    shutdown: tuple[ShutdownHook, ...]
