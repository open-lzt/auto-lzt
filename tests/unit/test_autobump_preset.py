"""The autobump preset compiles to a graph made of shipped nodes — asserted, not assumed."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.domain.catalog.plugins import build_registry
from app.domain.panel.presets import AutobumpSettings, NoAccountsSelected, build_autobump_flow


def _settings(**overrides: object) -> AutobumpSettings:
    base: dict[str, object] = {
        "accounts": (uuid4(), uuid4()),
        "schedule_cron": "*/30 * * * *",
        "max_bumps": 5,
        "reprice": False,
    }
    base.update(overrides)
    return AutobumpSettings(**base)  # type: ignore[arg-type]


def test_every_node_the_preset_emits_is_one_the_engine_already_runs() -> None:
    """The claim this feature makes about itself: it is composition, not a second engine.

    Asserted against the real registry rather than a hand-written list, so a preset that starts
    depending on a node nobody registered fails here instead of at the first fire.
    """
    known = set(build_registry(load_plugins=False).node_classes())

    spec = build_autobump_flow("Поднятие", _settings(reprice=True))

    unknown = {node.type for node in spec.nodes} - known
    assert unknown == set()


def test_the_graph_is_wired_account_to_lots_to_limit_to_bump() -> None:
    spec = build_autobump_flow("Поднятие", _settings())
    by_id = {node.id: node for node in spec.nodes}

    assert spec.entry_node_id == "accounts"
    assert by_id["accounts"].edges == {"body": "lots"}
    assert by_id["lots"].edges == {"next": "limit"}
    assert by_id["limit"].edges == {"next": "each_lot"}
    assert by_id["each_lot"].edges == {"body": "bump"}


def test_the_bump_limit_reaches_the_graph_rather_than_only_the_form() -> None:
    """A cap that lives only in the UI is decoration. This pins it to the node that enforces it."""
    spec = build_autobump_flow("Поднятие", _settings(max_bumps=1))
    limit = next(node for node in spec.nodes if node.id == "limit")

    assert limit.type == "logic.take"
    assert limit.inputs["count"].literal == 1
    assert limit.inputs["items"].ref == "lots.item_ids"


def test_the_selected_accounts_are_what_the_loop_iterates() -> None:
    accounts = (uuid4(), uuid4(), uuid4())
    spec = build_autobump_flow("Поднятие", _settings(accounts=accounts))

    literal = next(n for n in spec.nodes if n.id == "accounts").inputs["account_ids"].literal
    assert json.loads(str(literal)) == [str(a) for a in accounts]


def test_reprice_is_absent_unless_asked_for() -> None:
    # Not cosmetic: an unwanted reprice node would rewrite the seller's prices on every fire.
    without = build_autobump_flow("Поднятие", _settings(reprice=False))
    with_reprice = build_autobump_flow("Поднятие", _settings(reprice=True))

    assert all(node.type != "market.reprice" for node in without.nodes)
    assert any(node.type == "market.reprice" for node in with_reprice.nodes)
    assert next(n for n in without.nodes if n.id == "bump").edges == {}


def test_an_empty_account_list_is_refused_at_deploy() -> None:
    """Refused here rather than at fire time: a flow that silently does nothing every 30 minutes is
    much harder to notice than a form that will not submit."""
    with pytest.raises(NoAccountsSelected):
        build_autobump_flow("Поднятие", _settings(accounts=()))
