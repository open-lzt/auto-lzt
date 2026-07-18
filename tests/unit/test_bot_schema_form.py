"""The bot's form, derived from the node's own JSON Schema (T3.2, success criterion 2).

The property under test is that there is NO per-node knowledge in the bot. The strongest way to
assert that is to render forms from the REAL catalog — every node the registry has, including a
plugin's — and to check that changing a node's Input model changes the form with zero bot edits.
So the last test does exactly that: it invents a node the bot has never heard of and renders it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.bot.render.schema_form import (
    FieldValueInvalid,
    UiKind,
    build_form,
    parse_value,
    render_prompt,
)
from tests.fixtures.flow_fakes import builtin_registry


def _form_for(model: type[BaseModel], key: str = "x.y") -> Any:
    return build_form(key, model.model_json_schema())


def test_the_ui_hint_chooses_the_control() -> None:
    class M(BaseModel):
        item_id: int = Field(title="Лот", json_schema_extra={"ui": "lot_ref"})

    field = _form_for(M).fields[0]
    assert field.ui is UiKind.LOT_REF
    assert field.label == "Лот"


def test_an_unknown_ui_degrades_to_text_rather_than_crashing() -> None:
    """A node newer than this bot — or a plugin's — can name a control that does not exist here. A
    form rendering a field slightly wrong is a bad afternoon; a bot that refuses to render because
    a plugin invented a control is a bot a plugin can switch off."""

    class M(BaseModel):
        thing: str = Field(json_schema_extra={"ui": "holographic_dial"})

    assert _form_for(M).fields[0].ui is UiKind.TEXT


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [(int, UiKind.NUMBER), (float, UiKind.NUMBER), (bool, UiKind.BOOL), (str, UiKind.TEXT)],
)
def test_a_field_with_no_hint_falls_back_to_its_json_schema_type(
    annotation: type, expected: UiKind
) -> None:
    M = type("M", (BaseModel,), {"__annotations__": {"v": annotation}})
    assert _form_for(M).fields[0].ui is expected


def test_an_enum_renders_as_a_select_with_its_choices() -> None:
    """Pydantic emits an enum as a $ref into $defs, so the choices live one hop away. Without
    following it, every enum would render as an unconstrained text box — and the operator would
    find out it was wrong only when the run failed."""
    from app.domain.catalog.nodes.operators import ComparisonOp

    class M(BaseModel):
        op: ComparisonOp

    field = _form_for(M).fields[0]
    assert field.ui is UiKind.SELECT
    assert "eq" in field.choices
    assert "is_null" in field.choices


def test_an_optional_field_is_not_required_and_still_knows_its_type() -> None:
    """``str | None`` becomes anyOf[string, null]. Reading the null branch as the type would make
    every optional field a text box."""

    class M(BaseModel):
        price: int | None = Field(default=None, json_schema_extra={"ui": "number"})

    field = _form_for(M).fields[0]
    assert field.required is False
    assert field.ui is UiKind.NUMBER


def test_a_secret_field_is_marked_so_the_chat_does_not_echo_it() -> None:
    from app.domain.catalog.nodes.telegram.send_message import SendMessageInput

    token = next(f for f in _form_for(SendMessageInput).fields if f.name == "bot_token")
    assert token.ui is UiKind.SECRET
    assert token.secret is True


def test_required_and_optional_are_read_from_the_schema_not_guessed() -> None:
    class M(BaseModel):
        needed: str
        spare: str = "x"

    form = _form_for(M)
    assert {f.name: f.required for f in form.fields} == {"needed": True, "spare": False}


@pytest.mark.parametrize("word", ["да", "Yes", "1", "true", "вкл"])
def test_a_bool_accepts_the_words_a_person_actually_types(word: str) -> None:
    class M(BaseModel):
        flag: bool = Field(json_schema_extra={"ui": "bool"})

    assert parse_value(_form_for(M).fields[0], word) is True


@pytest.mark.parametrize("word", ["нет", "No", "0", "false", "выкл"])
def test_a_bool_accepts_the_negative_words_too(word: str) -> None:
    class M(BaseModel):
        flag: bool = Field(json_schema_extra={"ui": "bool"})

    assert parse_value(_form_for(M).fields[0], word) is False


def test_a_bool_that_is_neither_is_an_error_not_a_silent_false() -> None:
    """``bool("maybe")`` is True, which is how a typo becomes a setting nobody chose."""

    class M(BaseModel):
        flag: bool = Field(json_schema_extra={"ui": "bool"})

    with pytest.raises(FieldValueInvalid):
        parse_value(_form_for(M).fields[0], "может быть")


def test_a_lot_ref_parses_to_an_int() -> None:
    class M(BaseModel):
        item_id: int = Field(json_schema_extra={"ui": "lot_ref"})

    assert parse_value(_form_for(M).fields[0], " 4321 ") == 4321


def test_a_number_that_is_not_a_number_is_refused() -> None:
    class M(BaseModel):
        item_id: int = Field(json_schema_extra={"ui": "lot_ref"})

    with pytest.raises(FieldValueInvalid) as exc:
        parse_value(_form_for(M).fields[0], "все мои лоты")
    assert exc.value.ui is UiKind.LOT_REF


def test_an_account_ref_must_be_a_real_id() -> None:
    """An account id is a UUID. Passing "мой основной" straight through would fail deep inside a
    node, with an error about nothing the operator typed."""

    class M(BaseModel):
        account: str = Field(json_schema_extra={"ui": "account_ref"})

    field = _form_for(M).fields[0]
    assert parse_value(field, "0f2b2d1e-0000-4000-8000-000000000001") == (
        "0f2b2d1e-0000-4000-8000-000000000001"
    )
    with pytest.raises(FieldValueInvalid):
        parse_value(field, "мой основной")


def test_a_value_outside_a_selects_choices_is_refused() -> None:
    from app.domain.catalog.nodes.operators import ComparisonOp

    class M(BaseModel):
        op: ComparisonOp

    with pytest.raises(FieldValueInvalid):
        parse_value(_form_for(M).fields[0], "definitely_not_an_operator")


def test_an_optional_field_can_be_skipped_but_a_required_one_cannot() -> None:
    class M(BaseModel):
        spare: str | None = Field(default=None, json_schema_extra={"ui": "text"})
        needed: str = Field(json_schema_extra={"ui": "text"})

    fields = {f.name: f for f in _form_for(M).fields}
    assert parse_value(fields["spare"], "-") is None
    assert parse_value(fields["needed"], "-") == "-"  # a literal dash is a valid string


def test_every_node_in_the_real_catalog_renders() -> None:
    """No node may be un-renderable. This walks the actual registry rather than a fixture, so a
    node added tomorrow is covered — and if someone gives a field a hint the vocabulary does not
    have, the fallback keeps the bot working rather than breaking the whole form."""
    for node_type in builtin_registry().all():
        form = build_form(node_type.key, node_type.input_schema.model_json_schema())
        for field in form.fields:
            assert isinstance(field.ui, UiKind)
            assert field.label, f"{node_type.key}.{field.name} has no label"
            assert render_prompt(field), f"{node_type.key}.{field.name} renders an empty prompt"


def test_the_money_nodes_form_names_its_field_in_russian() -> None:
    """The labels are the node author's job (T1.4 put them in the schema); the bot only shows what
    it is given. If this ever reads 'item_id', the schema lost its title, not the bot."""
    bump = builtin_registry().get("market.bump")
    field = build_form("market.bump", bump.input_schema.model_json_schema()).fields[0]

    assert field.label == "Лот"
    assert field.ui is UiKind.LOT_REF


def test_a_node_the_bot_has_never_heard_of_renders_anyway() -> None:
    """Success criterion 2, stated as a test: the bot has no per-node knowledge to update.

    This model does not exist anywhere in the codebase — it stands in for a plugin's node, or for
    tomorrow's built-in. The form comes out right with zero edits to the bot.
    """

    class SomeFuturePluginInput(BaseModel):
        account: int = Field(title="Аккаунт", json_schema_extra={"ui": "account_ref"})
        note: str = Field(title="Заметка", json_schema_extra={"ui": "text"})
        dry_run: bool = Field(
            default=True, title="Пробный прогон", json_schema_extra={"ui": "bool"}
        )

    form = build_form("future.thing", SomeFuturePluginInput.model_json_schema())

    assert [(f.name, f.ui, f.required) for f in form.fields] == [
        ("account", UiKind.ACCOUNT_REF, True),
        ("note", UiKind.TEXT, True),
        ("dry_run", UiKind.BOOL, False),
    ]
    assert parse_value(form.fields[0], "0f2b2d1e-0000-4000-8000-000000000001") == (
        "0f2b2d1e-0000-4000-8000-000000000001"
    )
    assert parse_value(form.fields[2], "нет") is False


def test_removing_a_field_from_a_node_removes_it_from_the_form() -> None:
    """The other half of criterion 2: the form tracks the schema in both directions, so a field
    deleted from a node's Input cannot linger in the bot asking for something nobody wants."""

    class Before(BaseModel):
        keep: str
        drop: str

    class After(BaseModel):
        keep: str

    assert [f.name for f in _form_for(Before).fields] == ["keep", "drop"]
    assert [f.name for f in _form_for(After).fields] == ["keep"]


def test_the_prompt_tells_the_operator_what_a_select_will_accept() -> None:
    from app.domain.catalog.nodes.operators import ComparisonOp

    class M(BaseModel):
        op: ComparisonOp = Field(title="Операция")

    prompt = render_prompt(_form_for(M).fields[0])
    assert "Операция" in prompt
    assert "eq" in prompt


def test_the_prompt_says_a_secret_will_not_be_shown() -> None:
    from app.domain.catalog.nodes.telegram.send_message import SendMessageInput

    token = next(f for f in _form_for(SendMessageInput).fields if f.name == "bot_token")
    assert "не будет показано" in render_prompt(token)
