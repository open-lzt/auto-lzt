"""Compiles autobump settings into a FlowSpec built from shipped nodes.

The panel's «Поднятие» screen is not a second engine — it is a form that writes a flow. Everything
it produces is a graph the canvas can open, edit and re-deploy, which is the property that keeps the
preset from becoming a parallel product: there is one execution model, and the preset is one way to
author for it.

The graph it emits:

    for-each-account -> get-my-lots -> take(N) -> for-each-lot -> bump [-> reprice]

``take`` is the only node here that is not older than this feature; see its module docstring for why
bounding a fan-out could not be expressed without it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import AppError, ErrorCode
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec

# Node ids are `^\w+$` (see NodeSpec), so no dashes. They are stable strings rather than generated
# ones so a redeployed preset produces a diffable graph instead of a fresh set of ids each time.
_ACCOUNTS = "accounts"
_LOTS = "lots"
_LIMIT = "limit"
_EACH_LOT = "each_lot"
_BUMP = "bump"
_REPRICE = "reprice"


class NoAccountsSelected(AppError):
    """Deploying an autobump preset with an empty account list.

    Refused at deploy rather than at fire time: a flow that compiles, schedules, and then does
    nothing every 30 minutes is far harder to notice than a form that will not submit.
    """

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self) -> None:
        super().__init__("autobump preset needs at least one account")

    @property
    def client_message(self) -> str:
        return "Выберите хотя бы один аккаунт"


@dataclass(slots=True, frozen=True)
class AutobumpSettings:
    """What the «Поднятие» form collects.

    ``max_bumps`` is a per-FIRE cap on how many lots are bumped, not a rolling quota over an hour or
    a day: the engine has no run-history predicate a graph could read, and the cron period is what
    actually paces the bumping. Naming it per-fire here keeps the UI from implying a guarantee the
    graph does not make.
    """

    accounts: tuple[UUID, ...]
    schedule_cron: str
    max_bumps: int
    reprice: bool
    reprice_currency: str = "rub"
    reprice_price: float | None = None


def build_autobump_flow(name: str, settings: AutobumpSettings) -> FlowSpec:
    """The settings as a graph. Pure — no I/O, so the shape is testable without a database."""
    if not settings.accounts:
        raise NoAccountsSelected()

    nodes = [
        NodeSpec(
            id=_ACCOUNTS,
            type="logic.for_each_account",
            inputs={
                "account_ids": InputSpec(literal=json.dumps([str(a) for a in settings.accounts]))
            },
            # "body" is the per-iteration edge the interpreter walks once per account; see
            # ForEachLotNode's docstring on the fan-out protocol.
            edges={"body": _LOTS},
        ),
        NodeSpec(
            id=_LOTS,
            # No inputs: get-my-lots always lists the pinned owner account, which under
            # for-each-account is whichever account this iteration is running as.
            type="logic.get_my_lots",
            edges={"next": _LIMIT},
        ),
        NodeSpec(
            id=_LIMIT,
            type="logic.take",
            inputs={
                "items": InputSpec(ref=f"{_LOTS}.item_ids"),
                "count": InputSpec(literal=settings.max_bumps),
            },
            edges={"next": _EACH_LOT},
        ),
        NodeSpec(
            id=_EACH_LOT,
            type="logic.for_each_lot",
            inputs={"item_ids": InputSpec(ref=f"{_LIMIT}.items")},
            edges={"body": _BUMP},
        ),
        NodeSpec(
            id=_BUMP,
            type="market.bump",
            inputs={"item_id": InputSpec(ref=f"{_EACH_LOT}.item_id")},
            edges={"next": _REPRICE} if settings.reprice else {},
        ),
    ]

    if settings.reprice:
        reprice_inputs = {
            "item_id": InputSpec(ref=f"{_EACH_LOT}.item_id"),
            "currency": InputSpec(literal=settings.reprice_currency),
        }
        if settings.reprice_price is not None:
            reprice_inputs["price"] = InputSpec(literal=settings.reprice_price)
        nodes.append(NodeSpec(id=_REPRICE, type="market.reprice", inputs=reprice_inputs))

    return FlowSpec(name=name, nodes=nodes, entry_node_id=_ACCOUNTS)
