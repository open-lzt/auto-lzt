"""The preset seam: what an installed preset pack can and cannot do to this build.

Entry points are faked by monkeypatching ``entry_points`` in the module under test — the same shape
`test_plugin_runtime` uses. Installing a real distribution per case would test pip, not the seam.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.flow_engine.spec import FlowSpec
from app.domain.panel.preset_plugins import (
    DuplicatePresetKey,
    PresetLoadFailed,
    build_presets,
)
from app.domain.panel.preset_registry import PresetParams, PresetSpec, UnknownPreset


def _build(name: str, params: Any) -> FlowSpec:
    raise AssertionError("not called: these tests never deploy")


def _spec(key: str) -> PresetSpec:
    return PresetSpec(
        key=key,
        title="Раздача",
        icon="gift",
        params=PresetParams,
        build=_build,
        default_name="Раздача",
    )


class _EntryPoint:
    """Enough of ``importlib.metadata.EntryPoint`` for the loader: a name, a dist and ``load()``."""

    dist = None

    def __init__(self, name: str, loaded: object) -> None:
        self.name = name
        self._loaded = loaded

    def load(self) -> object:
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def _with_eps(monkeypatch: pytest.MonkeyPatch, eps: list[object]) -> None:
    monkeypatch.setattr("app.domain.panel.preset_plugins.entry_points", lambda group: eps)


def test_an_installed_pack_adds_its_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_eps(monkeypatch, [_EntryPoint("pack", [_spec("giveaway")])])

    presets = build_presets()

    assert presets.get("giveaway").key == "giveaway"
    # Still there: a pack adds, it does not replace the shipped set.
    assert presets.get("autobump").key == "autobump"


def test_a_pack_preset_is_stamped_with_its_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Origin is what a collision message names, so it must come from the loader rather than from
    the pack's own declaration — a pack claiming `builtin` would point the error at us."""
    _with_eps(monkeypatch, [_EntryPoint("pack", [_spec("giveaway")])])

    assert build_presets().get("giveaway").origin == "pack"
    assert build_presets().get("autobump").origin == "builtin"


def test_a_pack_claiming_a_shipped_key_fails_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not last-wins. A pack silently replacing «Автобай» would deploy a different graph under a
    name the operator already trusts — and that preset spends money."""
    _with_eps(monkeypatch, [_EntryPoint("pack", [_spec("autobuy")])])

    with pytest.raises(DuplicatePresetKey) as caught:
        build_presets()

    assert caught.value.key == "autobuy"
    assert caught.value.incumbent == "builtin"
    assert caught.value.incoming == "pack"


def test_a_pack_that_cannot_be_imported_fails_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed, like the node seam: a preset dropped with a log line means the panel offers a
    set nobody declared, and the operator finds out when a form is missing."""
    _with_eps(monkeypatch, [_EntryPoint("pack", RuntimeError("import failed"))])

    with pytest.raises(PresetLoadFailed):
        build_presets()


def test_a_pack_advertising_the_wrong_type_fails_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_eps(monkeypatch, [_EntryPoint("pack", ["not a preset"])])

    with pytest.raises(PresetLoadFailed):
        build_presets()


def test_a_pack_advertising_nothing_fails_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty list is a packaging mistake, not a valid "no presets": the distribution declared an
    entry point on purpose."""
    _with_eps(monkeypatch, [_EntryPoint("pack", [])])

    with pytest.raises(PresetLoadFailed):
        build_presets()


def test_a_callable_entry_point_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pack may advertise a factory so its presets are built at load time rather than at import —
    the node seam accepts the same two shapes."""
    _with_eps(monkeypatch, [_EntryPoint("pack", lambda: [_spec("giveaway")])])

    assert build_presets().get("giveaway").origin == "pack"


def test_plugins_can_be_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_plugins=False` is what lets a test — and the module CLI — reason about the shipped set
    alone, with whatever happens to be installed in the environment out of the picture."""
    _with_eps(monkeypatch, [_EntryPoint("pack", [_spec("giveaway")])])

    with pytest.raises(UnknownPreset):
        build_presets(load_plugins=False).get("giveaway")
