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
_EACH_THREAD = "each_thread"
_BUMP_THREAD = "bump_thread"
_SEARCH = "search"
_BUY = "buy"


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


class NoThreadsSelected(AppError):
    """Deploying a thread-bump preset with no threads — refused for the same reason as
    ``NoAccountsSelected``: a schedule that fires forever and does nothing is invisible."""

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self) -> None:
        super().__init__("thread-bump preset needs at least one thread")

    @property
    def client_message(self) -> str:
        return "Укажите хотя бы одну тему"


@dataclass(slots=True, frozen=True)
class ThreadBumpSettings:
    """What the «Поднятие тем» form collects.

    ``threads`` is an explicit id list rather than "everything I posted": every pylzt method
    that ENUMERATES threads (``threads_list``, ``threads_recent``, ``threads_followed``)
    returns an unparsed ``str``, so an auto-discovered list could not be relied upon. Naming
    the threads is the honest surface; the panel resolves each id to its title through
    ``threads_get`` so the operator still reads names rather than numbers.
    """

    accounts: tuple[UUID, ...]
    threads: tuple[int, ...]
    schedule_cron: str


def build_thread_bump_flow(name: str, settings: ThreadBumpSettings) -> FlowSpec:
    """The «Поднятие тем» settings as a graph. Pure — no I/O, so the shape is testable.

        for-each-account -> for-each-thread -> bump-thread

    The thread list is a literal on the fan-out node rather than a lookup at run time, which is
    what keeps the deployed flow readable in the canvas: the threads it bumps are written in it.
    """
    if not settings.accounts:
        raise NoAccountsSelected()
    if not settings.threads:
        raise NoThreadsSelected()

    nodes = [
        NodeSpec(
            id=_ACCOUNTS,
            type="logic.for_each_account",
            inputs={
                "account_ids": InputSpec(literal=json.dumps([str(a) for a in settings.accounts]))
            },
            edges={"body": _EACH_THREAD},
        ),
        NodeSpec(
            id=_EACH_THREAD,
            # Reuses for_each_lot: it fans a JSON array of ints out one per iteration, which is
            # exactly what a thread list is. A near-identical for_each_thread would add a second
            # way to express one idea and nothing else.
            type="logic.for_each_lot",
            inputs={"item_ids": InputSpec(literal=json.dumps(list(settings.threads)))},
            edges={"body": _BUMP_THREAD},
        ),
        NodeSpec(
            id=_BUMP_THREAD,
            type="forum.bump_thread",
            inputs={"thread_id": InputSpec(ref=f"{_EACH_THREAD}.item_id")},
        ),
    ]
    return FlowSpec(name=name, nodes=nodes, entry_node_id=_ACCOUNTS)


class NoLotsRequested(AppError):
    """An autobuy preset asked to buy fewer than one lot."""

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self) -> None:
        super().__init__("autobuy preset needs a positive count")

    @property
    def client_message(self) -> str:
        return "Укажите, сколько лотов покупать"


@dataclass(slots=True, frozen=True)
class AutobuySettings:
    """What the «Автобай» form collects.

    ``dry_run`` defaults to True and the form ships it on: this preset spends money, and a
    first deploy that quietly starts buying because a checkbox went unnoticed is the one
    failure worth designing against.
    """

    category: str
    max_price: float
    count: int
    schedule_cron: str
    dry_run: bool = True
    accounts: tuple[UUID, ...] = ()


def build_autobuy_flow(name: str, settings: AutobuySettings) -> FlowSpec:
    """The «Автобай» settings as a graph. Pure — no I/O.

        [for-each-account ->] search -> take(N) -> for-each-lot -> fast-buy

    ``search`` filters by price ON the marketplace, so a lot above the ceiling never reaches
    the buy node: the price cap is enforced upstream of the money, not by the buyer.
    """
    if settings.count < 1:
        raise NoLotsRequested()

    buy_chain = [
        NodeSpec(
            id=_SEARCH,
            type="market.search",
            inputs={
                "max_price": InputSpec(literal=settings.max_price),
                "category": InputSpec(literal=settings.category),
            },
            edges={"next": _LIMIT},
        ),
        NodeSpec(
            id=_LIMIT,
            type="logic.take",
            inputs={
                "items": InputSpec(ref=f"{_SEARCH}.item_ids"),
                "count": InputSpec(literal=settings.count),
            },
            edges={"next": _EACH_LOT},
        ),
        NodeSpec(
            id=_EACH_LOT,
            type="logic.for_each_lot",
            inputs={"item_ids": InputSpec(ref=f"{_LIMIT}.items")},
            edges={"body": _BUY},
        ),
        NodeSpec(
            id=_BUY,
            type="market.fast_buy",
            inputs={
                "item_id": InputSpec(ref=f"{_EACH_LOT}.item_id"),
                "dry_run": InputSpec(literal=settings.dry_run),
            },
        ),
    ]

    if not settings.accounts:
        # Nothing pinned: the tenant's pooled client picks the credential. Fine for buying —
        # unlike a thread bump, a purchase is not tied to one identity.
        return FlowSpec(name=name, nodes=buy_chain, entry_node_id=_SEARCH)

    accounts_node = NodeSpec(
        id=_ACCOUNTS,
        type="logic.for_each_account",
        inputs={"account_ids": InputSpec(literal=json.dumps([str(a) for a in settings.accounts]))},
        edges={"body": _SEARCH},
    )
    return FlowSpec(name=name, nodes=[accounts_node, *buy_chain], entry_node_id=_ACCOUNTS)
