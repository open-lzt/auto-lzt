"""Every preset declares a usable form and compiles to shipped nodes.

The point of the preset registry is that a preset's fields exist in exactly ONE place. These tests
pin the two ways that promise can silently break: a preset whose declared form is empty (the panel
would render a submit button and nothing else), and a preset whose graph uses a node this build
does not register (it would fail at the first fire, holding a schedule).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.catalog.plugins import build_registry
from app.domain.panel.preset_registry import (
    BUILTIN_PRESETS,
    AutobumpParams,
    AutobuyParams,
    PresetSpec,
    SchedulePreset,
    ThreadBumpParams,
    UnknownPreset,
    get_preset,
)

_PARAMS: dict[str, dict[str, object]] = {
    "autobump": {"accounts": [uuid4()]},
    "thread-bump": {"accounts": [uuid4()], "threads": [123456]},
    "autobuy": {},
}


@pytest.mark.parametrize("preset", BUILTIN_PRESETS, ids=lambda p: p.key)
def test_a_preset_declares_at_least_one_field(preset: PresetSpec) -> None:
    """An empty schema renders as a form with no inputs — a screen that cannot be filled in."""
    schema = preset.params.model_json_schema()

    assert schema.get("properties"), f"preset {preset.key} declares no fields"


@pytest.mark.parametrize("preset", BUILTIN_PRESETS, ids=lambda p: p.key)
def test_every_node_a_preset_emits_is_one_the_engine_registers(preset: PresetSpec) -> None:
    known = set(build_registry(load_plugins=False).node_classes())
    params = preset.params.model_validate(_PARAMS[preset.key])

    spec = preset.build(preset.default_name, params)

    assert {node.type for node in spec.nodes} - known == set()


@pytest.mark.parametrize("preset", BUILTIN_PRESETS, ids=lambda p: p.key)
def test_the_schedule_is_readable_off_any_preset_without_knowing_which(preset: PresetSpec) -> None:
    """The deploy route attaches the trigger, so it reads `schedule_cron` off whatever preset it
    is holding. That only works while every params model keeps the field on the shared base."""
    params = preset.params.model_validate(_PARAMS[preset.key])

    assert params.schedule_cron.value in {s.value for s in SchedulePreset}


def test_the_schedule_choices_carry_human_labels() -> None:
    """A cron expression is not a caption. Without `x-ui.options` the picker would offer
    «*/30 * * * *» as the visible text of an option."""
    schema = AutobumpParams.model_json_schema()
    ui = schema["properties"]["schedule_cron"]["x-ui"]

    labels = {opt["label"] for opt in ui["options"]}
    values = {opt["value"] for opt in ui["options"]}

    assert values == {s.value for s in SchedulePreset}
    assert "Каждые 30 минут" in labels


def test_pickers_are_declared_so_the_client_never_hardcodes_a_list() -> None:
    """Categories and accounts are fetched live by the widget the schema names. This is the
    assertion that stops the 21-slug TypeScript copy of SearchableCategory from coming back."""
    autobuy = AutobuyParams.model_json_schema()["properties"]
    threads = ThreadBumpParams.model_json_schema()["properties"]

    assert autobuy["category"]["x-ui"]["widget"] == "category_picker"
    assert autobuy["accounts"]["x-ui"]["widget"] == "account_ref"
    assert threads["accounts"]["x-ui"]["widget"] == "account_ref"


def test_autobuy_defaults_to_a_dry_run() -> None:
    """This preset spends money. The value you get by not touching the field must be the safe
    one, at the layer that decides it — the model, not the form."""
    assert AutobuyParams.model_validate({}).dry_run is True


def test_an_unknown_preset_key_is_a_typed_404() -> None:
    with pytest.raises(UnknownPreset):
        get_preset("does-not-exist")


def test_preset_keys_are_unique() -> None:
    keys = [preset.key for preset in BUILTIN_PRESETS]

    assert len(keys) == len(set(keys))
