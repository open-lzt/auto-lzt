"""Plugin discovery — third-party node types, found through ``importlib.metadata`` entry points.

A distribution adds nodes by advertising the ``lzt_flow.nodes`` group::

    [project.entry-points."lzt_flow.nodes"]
    my_pack = "my_pack.nodes:REGISTRATIONS"

The advertised object is a ``NodeRegistration``, an iterable of them, or a zero-argument callable
returning either. Installing the distribution is the entire install step; there is no plugin
directory to scan and no path to configure, so a node cannot appear in the registry without
somebody having installed a package that provides it.

**An install mechanism, not a security boundary.** ``ep.load()`` imports the plugin, and importing
runs its module body: arbitrary code, in this process, with its tokens and its money. Nothing here
is a sandbox, and reading it as one is the mistake worth naming. The duplicate-key rule below stops
a plugin from *registering* over ``market.bump``; it does not stop one from assigning to
``BumpNode.execute`` on import. There would be no collision, the origin would still read
``builtin``, and every flow on the stand would quietly run the new code.

That is an acceptable place to land: ``pip install`` is an administrator's act, and installing a
plugin trusts its author about as far as one trusts this engine. It is not an acceptable thing to
be vague about — which is why the community registry refuses to install ``kind: python`` over the
API at all (``domain/modules/``), where the installer is whoever holds the bot's API key rather
than the person at the shell.

**Fail-closed.** Every failure here raises at startup rather than being logged and skipped. A
process that silently drops a broken plugin serves a node set nobody declared: flows referencing
that node start failing at *run* time, holding money, instead of at boot. Refusing to start is the
louder and cheaper failure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from importlib.metadata import EntryPoint, entry_points
from typing import Final

from app.domain.catalog.registry import (
    BUILTIN_REGISTRATIONS,
    NodeRegistration,
    NodeRegistry,
)

ENTRY_POINT_GROUP: Final = "lzt_flow.nodes"


class PluginLoadFailed(Exception):
    """An advertised entry point could not be turned into registrations. Carries args, not
    formatted text."""

    def __init__(self, entry_point: str, origin: str, reason: str) -> None:
        super().__init__()
        self.entry_point = entry_point
        self.origin = origin
        self.reason = reason


def _origin_of(ep: EntryPoint) -> str:
    """The distribution that advertised ``ep``, for collision messages. ``EntryPoint.dist`` is None
    for hand-constructed entry points (tests), where the entry point's own name is the best
    available label."""
    dist = ep.dist
    return dist.name if dist is not None else ep.name


def _registrations_from(ep: EntryPoint) -> list[NodeRegistration]:
    origin = _origin_of(ep)
    try:
        loaded = ep.load()
    except Exception as exc:  # noqa: BLE001 — a plugin's import may raise anything; fail closed
        raise PluginLoadFailed(ep.name, origin, repr(exc)) from exc

    if callable(loaded) and not isinstance(loaded, NodeRegistration):
        try:
            loaded = loaded()
        except Exception as exc:  # noqa: BLE001 — same: the plugin's code, not ours
            raise PluginLoadFailed(ep.name, origin, repr(exc)) from exc

    items = [loaded] if isinstance(loaded, NodeRegistration) else loaded
    if not isinstance(items, Iterable):
        raise PluginLoadFailed(
            ep.name, origin, f"expected NodeRegistration or an iterable of them, got {type(items)}"
        )

    stamped: list[NodeRegistration] = []
    for item in items:
        if not isinstance(item, NodeRegistration):
            raise PluginLoadFailed(ep.name, origin, f"expected NodeRegistration, got {type(item)}")
        stamped.append(replace(item, origin=origin))
    if not stamped:
        raise PluginLoadFailed(ep.name, origin, "advertised no node registrations")
    return stamped


def load_plugin_registrations() -> list[NodeRegistration]:
    """Every registration advertised under ``ENTRY_POINT_GROUP``, origin-stamped. Raises
    ``PluginLoadFailed`` for a plugin that cannot be loaded — see the module docstring."""
    found: list[NodeRegistration] = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        found.extend(_registrations_from(ep))
    return found


def build_registry(
    *,
    load_plugins: bool = True,
    extra_registrations: Iterable[NodeRegistration] = (),
) -> NodeRegistry:
    """The process's node set: the built-ins, plus installed node packs unless opted out, plus any
    ``extra_registrations`` (where the plugin runtime injects a full plugin's contributed nodes).

    Built-ins are registered first, then ``lzt_flow.nodes`` packs, then ``extra_registrations`` —
    so a plugin claiming a built-in key loses deterministically: ``NodeRegistry`` reports the
    *second* registration as the incoming one, making the resulting ``DuplicateNodeType`` name the
    plugin as the shadower regardless of order.

    That decides *collisions*, which is a smaller claim than it sounds: it is what keeps two
    packages fighting over one key from resolving to last-wins silence, not what keeps a hostile
    package away from a built-in. See the module docstring.
    """
    registrations: list[NodeRegistration] = list(BUILTIN_REGISTRATIONS)
    if load_plugins:
        registrations.extend(load_plugin_registrations())
    registrations.extend(extra_registrations)
    return NodeRegistry(registrations)
