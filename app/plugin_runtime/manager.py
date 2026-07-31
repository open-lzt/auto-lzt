"""PluginManager — discover installed plugins, run their lifecycle, apply their contributions.

Two discovery sources feed one pipeline: `lzt_flow.plugins` entry points (deliberate `pip install`)
and `<plugin_dir>/<name>/` folders (installed from the bot). Lifecycle, per process: `discover()`
(sync, fail-closed for entry points / quarantine for folders) → `pre_init()` (sync, returns the
process-filtered contributions) → `post_init()` (async, live handles) → `shutdown()` (async).

Collision policy (D-4/F3): ONE gate, applied to both sources, with different reactions. An
entry-point plugin whose node key collides fails closed here (that path is an admin's shell act —
its ambiguity must not be served); a folder plugin's collision is **quarantined** (logged +
skipped), so a bot-installed plugin can never brick the boot the admin needs to remove it. The gate
used to run for folder plugins only, leaving entry-point collisions to `build_registry` — which does
refuse them, but only in the processes that keep nodes and with a message that names two node types
rather than the plugin that caused it.

Hook budget: every hook is somebody else's code, and a hook that never returns is indistinguishable
from a slow start. All three phases run under `_HOOK_TIMEOUT_S`; on timeout the plugin is treated
exactly as if the hook had raised.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from importlib.metadata import entry_points
from typing import Any, Final

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.catalog.registry import BUILTIN_REGISTRATIONS, NodeRegistration, NodeRegistry
from app.domain.panel.tabs import PanelTabSpec, stamp_origin
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
    PluginSettings,
    PluginSource,
    PostInitHook,
    PreInitHook,
    ShutdownHook,
    SpawnFn,
)
from app.plugin_runtime.errors import PluginHookError, PluginLoadError
from app.plugin_runtime.folder_source import load_folder_plugins

log = structlog.get_logger()

# One budget for every hook phase. Generous: a POST_INIT that opens a connection pool is doing real
# work. Anything past it is not slow, it is stuck — and a stuck sync PRE_INIT holds the whole
# process start, which is why even the sync phase gets a clock.
_HOOK_TIMEOUT_S: Final = 30.0

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


def _keep[T](items: list[T], when: bool) -> tuple[T, ...]:
    return tuple(items) if when else ()


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
        # Built once: every hook context hands out the same projection, and building it per
        # hook would be a second place the field list could drift.
        self._plugin_settings = PluginSettings.from_settings(settings)
        self._plugins: list[DiscoveredPlugin] = []
        # survivors of pre_init — post_init/shutdown iterate this, not the raw discovered set
        self._active: list[DiscoveredPlugin] = []
        self._tasks: list[asyncio.Task[None]] = []
        # Which plugin spawned which task, so a plugin quarantined at POST_INIT takes its own
        # background work down with it instead of leaving it running unowned.
        self._task_owner: dict[asyncio.Task[None], str] = {}
        self._ready: _ReadyHandles | None = None
        self._discovered = False
        # One executor for the whole PRE_INIT phase, not one per hook. Sized to the plugin count so
        # a hook that never returns costs its own worker and cannot starve the next plugin's.
        self._pre_init_pool: ThreadPoolExecutor | None = None

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
        policy. Entry-point plugins run first, so they claim keys first; both sources pass the same
        node-key gate. Sets the active set that post_init/shutdown iterate.

        Every PRE_INIT hook runs on a worker thread, NOT the main thread — a clock on synchronous
        third-party code is not otherwise possible. A hook that touches the running event loop,
        installs signal handlers, or reads a contextvar set on the main thread will not see what it
        expects; hooks needing any of that belong in POST_INIT, which is awaited on the loop.
        """
        claimed: set[str] = {reg.node_type.key for reg in BUILTIN_REGISTRATIONS}
        active: list[DiscoveredPlugin] = []
        nodes: list[NodeRegistration] = []
        api_routers: list[object] = []
        bot_routers: list[object] = []
        panel_tabs: list[PanelTabSpec] = []
        try:
            # False (entry-point) sorts before True (folder): entry points claim keys first.
            for plugin in sorted(self._plugins, key=lambda p: p.source is PluginSource.FOLDER):
                try:
                    loaded = [
                        self._run_pre_init_hook(plugin.name, hook) for hook in plugin.pre_init
                    ]
                    plugin_nodes = [
                        replace(reg, origin=plugin.name) for lc in loaded for reg in lc.nodes
                    ]
                    clash = next(
                        (r.node_type.key for r in plugin_nodes if r.node_type.key in claimed), None
                    )
                    if clash is not None:
                        raise PluginHookError(
                            plugin.name, "pre_init", f"node key {clash!r} already registered"
                        )
                except PluginHookError as exc:
                    if plugin.source is PluginSource.ENTRY_POINT:
                        raise
                    log.error("plugin.quarantined", plugin=plugin.name, reason=exc.reason)
                    continue
                claimed.update(r.node_type.key for r in plugin_nodes)
                active.append(plugin)
                nodes.extend(plugin_nodes)
                for lc in loaded:
                    api_routers.extend(lc.api_routers)
                    bot_routers.extend(lc.bot_routers)
                    panel_tabs.extend(stamp_origin(lc.panel_tabs, plugin.name))
            self._active = active
            return self._filter(nodes, api_routers, bot_routers, panel_tabs)
        finally:
            # In `finally` because the entry-point branch above re-raises: the pool has to go on the
            # fail-closed path too, not only when every plugin loaded.
            # wait=False: a hook that timed out left its thread running and cannot be killed, so
            # waiting here would hang the very start the timeout exists to protect.
            if self._pre_init_pool is not None:
                self._pre_init_pool.shutdown(wait=False)
                self._pre_init_pool = None

    def _run_pre_init_hook(self, plugin_name: str, hook: PreInitHook) -> PluginLoadedContext:
        """Call one sync PRE_INIT hook under the shared budget.

        A thread is the only way to put a clock on synchronous third-party code, and it is a
        one-way one: on timeout the thread is abandoned, not killed, because Python cannot kill it.
        That is still the right trade — the process finishes starting without the plugin instead of
        hanging forever on it, and the leaked thread is bounded by the number of plugins.

        The pool is the phase's, not this call's. Building one per hook meant the
        `shutdown(wait=False)` on timeout leaked the pool as well as the thread, and it made the
        thread count a function of how many hooks exist rather than how many can be stuck at once.
        """
        ctx = PluginLoadContext(
            process=self.process,
            plugin_name=plugin_name,
            settings=self._plugin_settings,
            logger=log.bind(plugin=plugin_name),
        )
        future = self._pre_init_executor().submit(hook, ctx)
        try:
            loaded = future.result(timeout=_HOOK_TIMEOUT_S)
        except FutureTimeoutError as exc:
            raise PluginHookError(
                plugin_name, "pre_init", f"hook exceeded {_HOOK_TIMEOUT_S}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — the plugin's code, fail closed
            raise PluginHookError(plugin_name, "pre_init", repr(exc)) from exc
        if not isinstance(loaded, PluginLoadedContext):
            raise PluginHookError(
                plugin_name,
                "pre_init",
                f"PRE_INIT hook returned {type(loaded)}, not PluginLoadedContext",
            )
        return loaded

    def _pre_init_executor(self) -> ThreadPoolExecutor:
        if self._pre_init_pool is None:
            self._pre_init_pool = ThreadPoolExecutor(
                max_workers=max(1, len(self._plugins)), thread_name_prefix="plugin-pre-init"
            )
        return self._pre_init_pool

    def _filter(
        self,
        nodes: list[NodeRegistration],
        api_routers: list[object],
        bot_routers: list[object],
        panel_tabs: list[PanelTabSpec],
    ) -> PluginContributions:
        """Keep only the surfaces this process actually consumes.

        The ``tuple(x) if keep else ()`` shape was written out three times before panel tabs
        arrived; adding a fourth field the obvious way would have made it four. ``_keep`` is the
        smallest fix that stops the repetition growing, and it costs nothing because this method was
        being edited anyway. Full generalization (iterating ``(field, predicate)`` pairs) would have
        to touch the three existing fields too — deliberately out of scope here, but see
        ``_MODULE.md``: a FIFTH surface is the point where the filter itself should be generalized
        rather than extended again.
        """
        keep_nodes = self.process in (PluginProcess.API, PluginProcess.WORKER)
        keep_api = self.process is PluginProcess.API
        keep_bot = self.process is PluginProcess.BOT
        return PluginContributions(
            nodes=_keep(nodes, keep_nodes),
            api_routers=_keep(api_routers, keep_api),  # type: ignore[arg-type]
            bot_routers=_keep(bot_routers, keep_bot),  # type: ignore[arg-type]
            # API only: it is the process that serves /panel/tabs to the browser.
            panel_tabs=_keep(panel_tabs, keep_api),
        )

    async def post_init(
        self,
        *,
        node_registry: NodeRegistry,
        redis: Redis | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Await every active plugin's POST_INIT with a ``PluginReadyContext``. Background tasks a
        hook starts via ``ctx.spawn`` are tracked for shutdown. A raise or a timeout is fatal for an
        entry-point plugin and quarantines a folder one (D-4).

        Quarantine here means: dropped from the active set, its spawned tasks cancelled, and its own
        SHUTDOWN hooks run so whatever the failed POST_INIT half-opened gets closed. It used to mean
        only a log line — the plugin stayed active, so `shutdown` later called SHUTDOWN hooks for a
        plugin whose POST_INIT never completed, and any task it had already spawned kept running.

        What it does NOT undo is the plugin's node registrations: `pre_init` returns those to the
        caller, which builds the `NodeRegistry` handed to this method, so by now they are fixed.
        Closing that gap means running POST_INIT before the registry is composed, which is a change
        to how the three process entry points wire the manager, not to the manager.
        """
        self._ready = (node_registry, redis, sessionmaker)
        survivors: list[DiscoveredPlugin] = []
        for plugin in self._active:
            ready = self._ready_context(plugin.name, node_registry, redis, sessionmaker)
            try:
                for hook in plugin.post_init:
                    await self._run_ready_hook(plugin.name, "post_init", hook, ready)
            except PluginHookError as exc:
                if plugin.source is PluginSource.ENTRY_POINT:
                    raise
                log.error(
                    "plugin.quarantined", plugin=plugin.name, phase="post_init", reason=exc.reason
                )
                await self._quarantine(plugin, ready)
                continue
            survivors.append(plugin)
        self._active = survivors

    async def _quarantine(self, plugin: DiscoveredPlugin, ready: PluginReadyContext) -> None:
        """Take back what a plugin started before its POST_INIT failed."""
        owned = [task for task, owner in self._task_owner.items() if owner == plugin.name]
        for task in owned:
            task.cancel()
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)
        for hook in plugin.shutdown:
            await self._run_best_effort(plugin.name, "shutdown", hook, ready)

    async def shutdown(self) -> None:
        """Cancel every spawned task (before hooks), then await each active plugin's SHUTDOWN in
        reverse order. A raising SHUTDOWN hook is logged and shutdown continues (best-effort)."""
        # Snapshot: `_on_task_done` removes finished tasks from `_tasks`, so iterating the live list
        # across an await would zip a stale result set against a shortened one.
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for task, result in zip(tasks, results, strict=True):
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
                await self._run_best_effort(plugin.name, "shutdown", hook, ready)

    async def _run_ready_hook(
        self,
        plugin_name: str,
        phase: str,
        hook: PostInitHook | ShutdownHook,
        ready: PluginReadyContext,
    ) -> None:
        """One async hook under the shared budget. Always raises ``PluginHookError`` on failure —
        whether that is fatal or a quarantine is the caller's decision, not this method's."""
        try:
            await asyncio.wait_for(hook(ready), _HOOK_TIMEOUT_S)
        except TimeoutError as exc:
            raise PluginHookError(plugin_name, phase, f"hook exceeded {_HOOK_TIMEOUT_S}s") from exc
        except Exception as exc:  # noqa: BLE001 — the plugin's code
            raise PluginHookError(plugin_name, phase, repr(exc)) from exc

    async def _run_best_effort(
        self,
        plugin_name: str,
        phase: str,
        hook: PostInitHook | ShutdownHook,
        ready: PluginReadyContext,
    ) -> None:
        """Teardown: one hook's failure must not stop the rest of the teardown from running."""
        try:
            await self._run_ready_hook(plugin_name, phase, hook, ready)
        except PluginHookError as exc:
            log.error("plugin.hook_failed", plugin=plugin_name, phase=phase, error=exc.reason)

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
            settings=self._plugin_settings,
            logger=log.bind(plugin=plugin_name),
            node_registry=node_registry,
            redis=redis,
            sessionmaker=sessionmaker,
            spawn=self._spawn_for(plugin_name),
        )

    def _spawn_for(self, plugin_name: str) -> SpawnFn:
        """``ctx.spawn`` bound to its plugin, so a quarantine can cancel that plugin's tasks and
        only that plugin's."""

        def spawn(coro: Coroutine[Any, Any, None], name: str) -> None:
            task = asyncio.ensure_future(coro)
            task.set_name(name)
            self._tasks.append(task)
            self._task_owner[task] = plugin_name
            task.add_done_callback(self._on_task_done)

        return spawn

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        # Drop it here: `_tasks` exists to cancel what is still running at shutdown, and a plugin
        # that spawns one task per tick would otherwise grow the list for the life of the process.
        if task in self._tasks:
            self._tasks.remove(task)
        self._task_owner.pop(task, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("plugin.task_crashed", task=task.get_name(), error=repr(exc))
