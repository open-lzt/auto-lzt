"""FastBuyNode — buys one lot by id. The only node in the catalog that spends money.

Sits behind ``for-each-lot``, so it receives one id per iteration and never sees the list.

``market.search``'s ``pmax`` filters server-side at SEARCH time, and that used to be the only price
control on the whole path — the buy node took an id and paid whatever the lot cost when it got
there. That is not a second source of truth for the same rule, it is the same rule at a different
instant: a seller can reprice between the search and the purchase, and the id that passed the
filter is then bought above the ceiling the operator set. ``max_price`` here is optional and
unset by default, so every existing flow keeps its current behaviour; when it IS set the adapter
reads the lot's current price and refuses the lot rather than the run (``LotUnavailable``), which
is the same "skip this lot, keep sniping" shape a contested lot already has.

``dry_run`` defaults to TRUE. A buy node that defaulted to spending would turn a mistyped flow into
a purchase, and the whole point of the testnet-first stance is that the expensive default is opt-in.

"Never buys the same lot twice" rests on TWO signals, because one of them expires. The redis
idempotency key is consumed before the effect — precise, but gone after its TTL, a flush, or a
restart of a Redis with no persistence, and the branch that refuses to re-buy was reachable only
while that key lived. ``ctx.step_replay`` is the durable half: the ``RunStep`` row in Postgres,
written by the runtime before the node runs and outliving all three. It is coarser — it fires for
a crash BEFORE the purchase too — so a resume can be refused with no money having moved. That is
the affordable direction: refusing costs one manual check, buying twice costs the lot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import structlog
from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_MUTATE_MONEY, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed
from app.domain.market.dtos import FastBuyResult
from app.domain.market.errors import LotUnavailable
from app.domain.purchases.model import Purchase, PurchaseId

logger = structlog.get_logger()


class FastBuyInput(BaseSchema):
    item_id: int = Field(gt=0, title="Лот", json_schema_extra={"x-ui": {"widget": "lot_ref"}})
    max_price: int | None = Field(
        default=None,
        gt=0,
        title="Платить не дороже",
        description="Пусто — платим сколько просят. Задано — перед оплатой сверяем текущую цену "
        "лота и пропускаем лот, если продавец поднял её выше этого потолка.",
        json_schema_extra={"x-ui": {"widget": "number"}},
    )
    max_price_currency: str | None = Field(
        default=None,
        title="Валюта потолка",
        description="Обязательна вместе с потолком: цена лота приходит в своей валюте, и потолок "
        "в другой валюте — не потолок. Лот в другой валюте пропускается, а не пересчитывается.",
        json_schema_extra={"x-ui": {"widget": "select"}},
    )
    dry_run: bool = Field(
        default=True,
        title="Холостой прогон",
        description="Включено — покупка не выполняется, узел только сообщает что купил бы.",
        json_schema_extra={"x-ui": {"widget": "switch"}},
    )


async def _record(ctx: RunContext, result: FastBuyResult) -> None:
    """Append the purchase to the ledger. NEVER lets a ledger failure fail the purchase.

    By the time this runs the money has left. Raising here would report a completed purchase as a
    failed step, and the engine's retry would then buy a SECOND lot — the same trade
    ``PurchaseOutcomeUnknown`` exists for, and the same direction: a missing ledger row is an
    accounting gap, a double purchase is a loss. So the write is best-effort and the failure is
    loud in the log instead of in the run.
    """
    try:
        recorded = await ctx.deps.purchases.record(
            Purchase(
                id=PurchaseId(uuid4()),
                tenant_id=ctx.tenant_id,
                item_id=result.item_id,
                price=Decimal(result.price),
                currency=result.currency,
                category_id=result.category_id,
                run_id=ctx.run_id,
                node_id=ctx.node.id,
                purchased_at=datetime.now(UTC),
            )
        )
    except Exception:  # noqa: BLE001 — see the docstring: money already moved, nothing may raise
        logger.exception(
            "purchase_ledger_write_failed", item_id=result.item_id, node_id=ctx.node.id
        )
        return
    if not recorded:
        # The step was replayed; the lot is already in the ledger. Normal, not an incident.
        logger.info("purchase_already_in_ledger", item_id=result.item_id, node_id=ctx.node.id)


class FastBuyOutput(BaseSchema):
    item_id: int
    price: int
    purchased: bool
    unavailable_reason: str = Field(
        default="",
        title="Почему не куплен",
        description="Заполняется, когда маркет отказал именно по этому лоту — например он уже в "
        "очереди на покупку у другого снайпера. Прогон при этом продолжается.",
    )


def as_int(value: str | int | float | bool | None, port: str) -> int:
    """Public because ``batch_submit`` runs its children's literals through the same gate — a
    batch child reaches the marketplace by reflection and never executes the owning node's code."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{port} must be an int, got {value!r}")
    return int(value)


def _as_ceiling(value: str | int | float | bool | None) -> int | None:
    """An unwired ``max_price`` means "no ceiling" — the behaviour every existing flow already has.

    A wired one must be a usable number: refusing here is free, whereas a ceiling that silently
    read as 0 or None would let the purchase through at any price, which is the failure this port
    exists to prevent.
    """
    if value is None:
        return None
    try:
        ceiling = as_int(value, "max_price")
    except ValueError as exc:
        # int("нет") raises naming neither the port nor the node — on a money guard the operator
        # must be told WHICH input they got wrong, not that some literal failed to parse.
        raise ValueError(f"max_price must be an int, got {value!r}") from exc
    if ceiling <= 0:
        raise ValueError(f"max_price must be positive, got {value!r}")
    return ceiling


def _as_currency(value: str | int | float | bool | None, max_price: int | None) -> str | None:
    """The unit the ceiling is stated in — required exactly when a ceiling is.

    A number without a unit cannot be compared to the lot's price, which arrives denominated in
    the lot's own currency. Leaving this optional would put the adapter back where it started:
    comparing 5000 against 50 and calling the result a ceiling.
    """
    if max_price is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"max_price_currency is required when max_price is set, got {value!r}")
    return value.strip()


_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "да"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "нет"})


def _as_bool(value: str | int | float | bool | None, port: str) -> bool:
    """Strict on this port, because the port it guards is ``dry_run``.

    This used to be `value.lower() in {"1","true","yes","on"}` with no else — so «да», "y", "1.0",
    "enabled" and "" all read as False, and False here means REAL MONEY LEAVES. The one coercion in
    this file where ambiguity is expensive was the one resolving ambiguity toward spending, while
    `_as_int` right above it raises on anything it cannot read. An unrecognised value on a money
    switch must stop the run, not buy.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
        raise ValueError(f"{port} must be a bool, got {value!r}")
    if isinstance(value, int | float):
        return bool(value)
    raise ValueError(f"{port} must be a bool, got {value!r}")


class FastBuyNode(BaseNode):
    node_type = "market.fast_buy"
    category = NodeCategory.ACTION
    idempotent = True
    # MONEY: must call guard.check_and_set before the effect; a contract test enforces it.
    capabilities = MARKET_MUTATE_MONEY
    input_schema = FastBuyInput
    output_schema = FastBuyOutput
    required_inputs = ("item_id",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        try:
            item_id = as_int(ctx.resolve_input("item_id"), "item_id")
            raw_dry_run = ctx.resolve_input("dry_run")
            dry_run = True if raw_dry_run is None else _as_bool(raw_dry_run, "dry_run")
            max_price = _as_ceiling(ctx.resolve_optional("max_price"))
            max_price_currency = _as_currency(ctx.resolve_optional("max_price_currency"), max_price)
        except ValueError as exc:
            # `relist` already raised RunFailed for the same class of defect while this node let a
            # bare ValueError out to be re-wrapped by the runtime — same failure, two shapes, and
            # the operator saw a nested message for one of them.
            raise RunFailed(ctx.run_id, ctx.node.id, str(exc)) from exc

        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first or ctx.step_replay:
            if dry_run:
                # No money can have moved on this key, so a replay is genuinely "nothing bought".
                return StepResultDTO(
                    node_id=ctx.node.id,
                    output={
                        "item_id": item_id,
                        "price": 0,
                        "purchased": False,
                        "deduplicated": True,
                    },
                )
            # Reaching execute() at all means the runtime found no COMPLETED RunStep for this step
            # (runtime.py returns the committed result before ever constructing the node when one
            # exists). So an earlier attempt reached this step and its outcome is unknown — the
            # PurchaseOutcomeUnknown case in this very file: the marketplace may have taken the
            # money after our client gave up. Reporting `purchased: false` told the operator the
            # one thing we cannot know. Same trade as relist.py: fail loudly, reconcile by hand.
            #
            # TWO independent signals, deliberately OR-ed. The redis guard is precise (it is set
            # immediately before the call) but expires — after its TTL, or a redis flush, or a
            # restart of a redis with no persistence, the same branch would happily buy a second
            # time. `ctx.step_replay` is the durable half: the RunStep row in Postgres, which
            # outlives all three. It is coarser — it says "we started this step before", which is
            # also true of a crash BEFORE the purchase — so a run can be blocked here without
            # money having moved. That direction is the affordable one: refusing costs an operator
            # one manual check, buying twice costs the lot's price.
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                f"fast_buy already attempted lot {item_id} on this step and the outcome was lost "
                "before it was committed; refusing to report it as not-bought and refusing to buy "
                "again — check the marketplace for this lot before retrying",
            )

        account_ref = ctx.active_account_id or ctx.node.account_ref
        try:
            if account_ref is not None:
                account = await ctx.deps.load_account(ctx.tenant_id, account_ref)
                result = await ctx.deps.market.fast_buy(
                    item_id,
                    account,
                    dry_run=dry_run,
                    max_price=max_price,
                    max_price_currency=max_price_currency,
                )
            else:
                result = await ctx.deps.market.fast_buy_via_pool(
                    ctx.tenant_id,
                    item_id,
                    dry_run=dry_run,
                    max_price=max_price,
                    max_price_currency=max_price_currency,
                )
        except LotUnavailable as exc:
            # Not a failure of the run: this one lot cannot be bought right now, usually because a
            # competing sniper queued it first. Cheap lots are contested, so aborting here meant a
            # sniper died on its first candidate and never reached the second.
            logger.info(
                "fast_buy_lot_unavailable",
                item_id=item_id,
                reason=exc.reason,
                node_id=ctx.node.id,
            )
            return StepResultDTO(
                node_id=ctx.node.id,
                output={
                    "item_id": item_id,
                    "price": 0,
                    "purchased": False,
                    "unavailable_reason": exc.reason or "маркет отказал по этому лоту",
                },
            )

        if result.purchased:
            await _record(ctx, result)

        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "item_id": result.item_id,
                "price": result.price,
                "purchased": result.purchased,
                "unavailable_reason": "",
            },
        )
