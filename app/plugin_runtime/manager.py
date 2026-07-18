"""PluginManager — discover installed plugins, run their lifecycle, apply their contributions.

Two discovery sources feed one pipeline: `lzt_flow.plugins` entry points (deliberate `pip install`)
and `<plugin_dir>/<name>/` folders (installed from the bot). Lifecycle, per process: `discover()`
(sync, fail-closed for entry points / quarantine for folders) → `pre_init()` (sync, returns the
process-filtered contributions) → `post_init()` (async, live handles) → `shutdown()` (async).

Collision policy (D-4/F3): an entry-point plugin whose node key collides fails closed downstream in
`build_registry` (that path is an admin's shell act — its ambiguity must not be served). A folder
plugin whose node key collides with a built-in or an already-accepted plugin is **quarantined** here
(logged + skipped), so a bot-installed plugin can never brick the boot the admin needs to remove it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from importlib.metadata import entry_points
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.catalog.registry import BUILTIN_REGISTRATIONS, NodeRegistration, NodeRegistry
from app.plugin_runtime.contracts import (
    ENTRY_POINT_GROUP,
    POST_INIT_ATTR,
    PRE_INIT_ATTR,
    SHUTDOWN_ATTR,
    DiscoveredPlugin,
    PluginContributions,
    PluginLoadContext,
    PluginLoadedContext,
    PluginProcess,
    PluginReadyContext,
    PluginSource,
    PostInitHook,
    PreInitHook,
    ShutdownHook,
)
from app.plugin_runtime.errors import PluginHookError, PluginLoadError
from app.plugin_runtime.folder_source import load_folder_plugins

log = structlog.get_logger()

# node_registry + the two Optional live handles, threaded from post_init to shutdown.
_ReadyHandles = tuple[NodeRegistry, Redis | None, async_sessionmaker[AsyncSession] | None]


def _read_hooks(module: object, attr: str, plugin_name: str) -> tuple[object, ...]:
    """The hook list declared on ``module`` as ``attr`` (default empty). Raises ``PluginLoadError``
    if it is not an iterable of callables — a malformed contract must fail at discovery, not when a
    hook is first called."""
    raw = getattr(module, attr, ())
    try:
        hooks = tuple(raw)
    except TypeError as exc:
        raise PluginLoadError(plugin_name, f"{attr} is not iterable: {raw!r}") from exc
    for hook in hooks:
        if not callable(hook):
            raise PluginLoadError(plugin_name, f"{attr} member is not callable: {hook!r}")
    return hooks


def _discovered(name: str, source: PluginSource, module: object) -> DiscoveredPlugin:
    return DiscoveredPlugin(
        name=name,
        source=source,
        pre_init=_read_hooks(module, PRE_INIT_ATTR, name),  # type: ignore[arg-type]
        post_init=_read_hooks(module, POST_INIT_ATTR, name),  # type: ignore[arg-type]
        shutdown=_read_hooks(module, SHUTDOWN_ATTR, name),  # type: ignore[arg-type]
    )


class PluginManager:
    def __init__(self, process: PluginProcess, settings: Settings) -> None:
        self.process = process
        self.settings = settings
        self._plugins: list[DiscoveredPlugin] = []
        # survivors of pre_init — post_init/shutdown iterate this, not the raw discovered set
        self._active: list[DiscoveredPlugin] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._ready: _ReadyHandles | None = None
        self._discovered = False

    def discover(self) -> None:
        """Read entry points AND scan `settings.plugin_dir`; import each, read the three hook lists.
        Entry-point import failure → `PluginLoadError` (fail-closed). Folder plugins that fail to
        load are quarantined inside `load_folder_plugins` (logged + skipped). Idempotent."""
        if self._discovered:
            return
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                module = ep.load()
            except Exception as exc:  # noqa: BLE001 — a plugin's import may raise anything; fail closed
                raise PluginLoadError(ep.name, repr(exc)) from exc
            self._plugins.append(_discovered(ep.name, PluginSource.ENTRY_POINT, module))
        loaded, _broken = load_folder_plugins(self.settings.plugin_dir)
        for fm in loaded:
            self._plugins.append(_discovered(fm.name, PluginSource.FOLDER, fm.module))
        self._discovered = True

    def pre_init(self) -> PluginContributions:
        """Run every plugin's PRE_INIT hooks; merge, stamp node origins, and apply the collision
        policy. Entry-point plugins run first and unchecked; folder plugins run second and are
        quarantined on a node-key collision. Sets the active set that post_init/shutdown iterate."""
        claimed: set[str] = {reg.node_type.key for reg in BUILTIN_REGISTRATIONS}
        active: list[DiscoveredPlugin] = []
        nodes: list[NodeRegistration] = []
        api_routers: list[object] = []
        bot_routers: list[object] = []
        # False (entry-point) sorts before True (folder): entry points claim keys first.
        for plugin in sorted(self._plugins, key=lambda p: p.source is PluginSource.FOLDER):
            loaded = [self._run_pre_init_hook(plugin.name, hook) for hook in plugin.pre_init]
            plugin_nodes = [replace(reg, origin=plugin.name) for lc in loaded for reg in lc.nodes]
            if plugin.source is PluginSource.FOLDER:
                clash = next(
                    (r.node_type.key for r in plugin_nodes if r.node_type.key in claimed), None
                )
                if clash is not None:
                    log.error(
                        "plugin.quarantined",
                        plugin=plugin.name,
                        reason=f"node key {clash!r} already registered",
                    )
                    continue
            claimed.update(r.node_type.key for r in plugin_nodes)
            active.append(plugin)
            nodes.extend(plugin_nodes)
            for lc in loaded:
                api_routers.extend(lc.api_routers)
                bot_routers.extend(lc.bot_routers)
        self._active = active
        return self._filter(nodes, api_routers, bot_routers)

    def _run_pre_init_hook(self, plugin_name: str, hook: PreInitHook) -> PluginLoadedContext:
        ctx = PluginLoadContext(
            process=self.process,
            plugin_name=plugin_name,
            settings=self.settings,
            logger=log.bind(plugin=plugin_name),
        )
        try:
            loaded = hook(ctx)
        except Exception as exc:  # noqa: BLE001 — the plugin's code, fail closed
            raise PluginHookError(plugin_name, "pre_init", repr(exc)) from exc
        if not isinstance(loaded, PluginLoadedContext):
            raise PluginHookError(
                plugin_name,
                "pre_init",
                f"PRE_INIT hook returned {type(loaded)}, not PluginLoadedContext",
            )
        return loaded

    def _filter(
        self, nodes: list[NodeRegistration], api_routers: list[object], bot_routers: list[object]
    ) -> PluginContributions:
        keep_nodes = self.process in (PluginProcess.API, PluginProcess.WORKER)
        keep_api = self.process is PluginProcess.API
        keep_bot = self.process is PluginProcess.BOT
        return PluginContributions(
            nodes=tuple(nodes) if keep_nodes else (),
            api_routers=tuple(api_routers) if keep_api else (),  # type: ignore[arg-type]
            bot_routers=tuple(bot_routers) if keep_bot else (),  # type: ignore[arg-type]
        )

    async def post_init(
        self,
        *,
        node_registry: NodeRegistry,
        redis: Redis | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Await every active plugin's POST_INIT with a ``PluginReadyContext``. Background tasks a
        hook starts via ``ctx.spawn`` are tracked for shutdown. A raise → ``PluginHookError``."""
        self._ready = (node_registry, redis, sessionmaker)
        for plugin in self._active:
            ready = self._ready_context(plugin.name, node_registry, redis, sessionmaker)
            for hook in plugin.post_init:
                await self._run_ready_hook(plugin.name, "post_init", hook, ready, fail_closed=True)

    async def shutdown(self) -> None:
        """Cancel every spawned task (before hooks), then await each active plugin's SHUTDOWN in
        reverse order. A raising SHUTDOWN hook is logged and shutdown continues (best-effort)."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            for task, result in zip(self._tasks, results, strict=True):
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    log.error("plugin.task_failed", task=task.get_name(), error=repr(result))
        self._tasks.clear()

        if self._ready is None:
            return  # post_init never ran — nothing was started, nothing to tear down
        node_registry, redis, sessionmaker = self._ready
        for plugin in reversed(self._active):
            ready = self._ready_context(plugin.name, node_registry, redis, sessionmaker)
            for hook in plugin.shutdown:
                await self._run_ready_hook(plugin.name, "shutdown", hook, ready, fail_closed=False)

    async def _run_ready_hook(
        self,
        plugin_name: str,
        phase: str,
        hook: PostInitHook | ShutdownHook,
        ready: PluginReadyContext,
        *,
        fail_closed: bool,
    ) -> None:
        try:
            await hook(ready)
        except Exception as exc:  # noqa: BLE001 — the plugin's code
            if fail_closed:
                raise PluginHookError(plugin_name, phase, repr(exc)) from exc
            log.error("plugin.shutdown_hook_failed", plugin=plugin_name, error=repr(exc))

    def _ready_context(
        self,
        plugin_name: str,
        node_registry: NodeRegistry,
        redis: Redis | None,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
    ) -> PluginReadyContext:
        return PluginReadyContext(
            process=self.process,
            plugin_name=plugin_name,
            settings=self.settings,
            logger=log.bind(plugin=plugin_name),
            node_registry=node_registry,
            redis=redis,
            sessionmaker=sessionmaker,
            spawn=self._spawn,
        )

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.ensure_future(coro)
        task.set_name(name)
        self._tasks.append(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("plugin.task_crashed", task=task.get_name(), error=repr(exc))
