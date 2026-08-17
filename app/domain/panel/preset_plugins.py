"""Preset discovery — third-party presets, found through ``importlib.metadata`` entry points.

A distribution adds presets by advertising the ``lzt_flow.presets`` group::

    [project.entry-points."lzt_flow.presets"]
    my_pack = "my_pack.presets:PRESETS"

Same shape, same failure modes and the same trust story as ``catalog/plugins.py`` — read that
module's docstring, it is the one that explains why an install is an administrator's act and why
every failure here stops the boot instead of being logged.

Why a seam at all, stated as arithmetic rather than taste: there are TWO preset sources today —
the three shipped here, and a private node pack that ships the scenario built from its own nodes.
A preset cannot live in that pack while ``BUILTIN_PRESETS`` is a closed tuple, so the pack would
have to patch the open repository to be usable, which is the fork this seam exists to avoid.

The node seam and this one stay separate groups on purpose. A pack may advertise both, but a pack
of nodes with no scenario is normal, and so is a scenario built entirely from shipped nodes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from importlib.metadata import EntryPoint, entry_points
from typing import Final

from app.domain.panel.preset_registry import BUILTIN_PRESETS, PresetSpec, UnknownPreset

ENTRY_POINT_GROUP: Final = "lzt_flow.presets"


class PresetLoadFailed(Exception):
    """An advertised entry point could not be turned into presets. Carries args, not formatted
    text — the boot log formats them once, with the distribution name attached."""

    def __init__(self, entry_point: str, origin: str, reason: str) -> None:
        super().__init__()
        self.entry_point = entry_point
        self.origin = origin
        self.reason = reason


class DuplicatePresetKey(Exception):
    """Two distributions claimed one preset key. Fails at import, like nodes and tabs.

    Names both sides: without the incumbent's origin the operator sees "duplicate 'giveaway'" and
    has to guess which two of their installed packages are fighting.
    """

    def __init__(self, key: str, incumbent: str, incoming: str) -> None:
        super().__init__(f"preset key {key!r} declared by both {incumbent!r} and {incoming!r}")
        self.key = key
        self.incumbent = incumbent
        self.incoming = incoming


def _origin_of(ep: EntryPoint) -> str:
    dist = ep.dist
    return dist.name if dist is not None else ep.name


def _presets_from(ep: EntryPoint) -> list[PresetSpec]:
    origin = _origin_of(ep)
    try:
        loaded = ep.load()
    except Exception as exc:  # noqa: BLE001 — a plugin's import may raise anything; fail closed
        raise PresetLoadFailed(ep.name, origin, repr(exc)) from exc

    if callable(loaded) and not isinstance(loaded, PresetSpec):
        try:
            loaded = loaded()
        except Exception as exc:  # noqa: BLE001 — same: the plugin's code, not ours
            raise PresetLoadFailed(ep.name, origin, repr(exc)) from exc

    items = [loaded] if isinstance(loaded, PresetSpec) else loaded
    if not isinstance(items, Iterable):
        raise PresetLoadFailed(
            ep.name, origin, f"expected PresetSpec or an iterable of them, got {type(items)}"
        )

    stamped: list[PresetSpec] = []
    for item in items:
        if not isinstance(item, PresetSpec):
            raise PresetLoadFailed(ep.name, origin, f"expected PresetSpec, got {type(item)}")
        # Origin is stamped here, never self-declared: a pack naming someone else as the origin of
        # its own preset would make the collision error point at the wrong package.
        stamped.append(replace(item, origin=origin))
    if not stamped:
        raise PresetLoadFailed(ep.name, origin, "advertised no presets")
    return stamped


class PresetRegistry:
    """The process's preset set, keyed and collision-checked once at boot."""

    def __init__(self, presets: Iterable[PresetSpec]) -> None:
        by_key: dict[str, PresetSpec] = {}
        ordered: list[PresetSpec] = []
        for preset in presets:
            existing = by_key.get(preset.key)
            if existing is not None:
                raise DuplicatePresetKey(preset.key, existing.origin, preset.origin)
            by_key[preset.key] = preset
            ordered.append(preset)
        self._by_key = by_key
        self._ordered = tuple(ordered)

    def all(self) -> tuple[PresetSpec, ...]:
        """Every preset, in registration order: built-ins first, then installed packs. The panel
        renders them in this order, so a pack lands after the shipped three rather than shuffling
        them on every restart."""
        return self._ordered

    def get(self, key: str) -> PresetSpec:
        preset = self._by_key.get(key)
        if preset is None:
            raise UnknownPreset(key)
        return preset


def load_plugin_presets() -> list[PresetSpec]:
    """Every preset advertised under ``ENTRY_POINT_GROUP``, origin-stamped."""
    found: list[PresetSpec] = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        found.extend(_presets_from(ep))
    return found


def build_presets(
    *,
    load_plugins: bool = True,
    extra_presets: Iterable[PresetSpec] = (),
) -> PresetRegistry:
    """The built-ins, plus installed preset packs unless opted out, plus ``extra_presets``.

    Built-ins are registered first, so a pack claiming a shipped key is reported as the incoming
    side of the collision regardless of load order — the same deterministic loss a plugin node
    takes against ``market.bump``.
    """
    presets: list[PresetSpec] = list(BUILTIN_PRESETS)
    if load_plugins:
        presets.extend(load_plugin_presets())
    presets.extend(extra_presets)
    return PresetRegistry(presets)
