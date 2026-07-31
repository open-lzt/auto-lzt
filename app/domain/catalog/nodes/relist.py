"""RelistNode — thin wrapper over ``MarketAdapter.publish`` /
``pylzt.Client.market.publishing_add``.

Publishing a new lot is **not idempotent** (00-decisions.md #3) — a retried call would create a
second, paid lot. ``relist`` always runs under a pinned owner account — a new lot must belong to
*someone*.

This node used to rely solely on the runtime's two-phase RunStep commit (F-1: INSERT RUNNING before
the effect, COMPLETE after). That is not enough: two-phase commit prevents *concurrent* double
execution, not crash-after-effect replay. A crash between ``publishing_add`` returning and
``complete_step`` leaves the step RUNNING; on resume ``claim_step`` conflicts, the orphan is not
COMPLETED, control falls through, and the lot is published a second time (T1.1b, 07-verification
V-1). So the effect is now guarded like every other money node.

Unlike ``bump``, the dedup path cannot return a real result: ``item_id`` is *produced* by the
effect, and the crash is exactly what lost it (the first attempt never reached ``complete_step``,
so no committed RunStep holds it). Echoing a placeholder id would silently poison any downstream
``${relist.item_id}`` reference, so a detected replay fails the run instead — the lot exists and is
paid for, and a human must reconcile it. Failing loudly is the honest trade for money.

The redis guard alone did NOT bound this: its key expires (and dies with a flush or a restart of a
Redis with no persistence), and past that point a resume republished a paid lot. So the check reads
``ctx.step_replay`` as well — the ``RunStep`` row in Postgres, written before the node runs and
outliving the key. It is coarser (it also fires for a crash BEFORE ``publishing_add``), so a resume
can be refused with nothing having been published; that is the cheap direction to be wrong in.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import Field
from pylzt.types import Currency, ItemOrigin

from app.core.schema import BaseSchema
from app.domain.account.errors import NoAvailableAccount
from app.domain.catalog.capabilities import MARKET_MUTATE_MONEY, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed


class RelistInput(BaseSchema):
    price: int = Field(title="Цена", json_schema_extra={"x-ui": {"widget": "number"}}, gt=0)
    category_id: int = Field(
        title="Категория", json_schema_extra={"x-ui": {"widget": "select"}}, gt=0
    )
    currency: str = Field(title="Валюта", json_schema_extra={"x-ui": {"widget": "select"}})
    item_origin: str = Field(
        title="Происхождение аккаунта", json_schema_extra={"x-ui": {"widget": "select"}}
    )
    title: str | None = Field(
        None,
        title="Заголовок",
        description="Пусто — берётся заголовок исходного лота.",
        json_schema_extra={"x-ui": {"widget": "text"}},
    )
    description: str | None = Field(
        None, title="Описание", json_schema_extra={"x-ui": {"widget": "text"}}
    )


class RelistOutput(BaseSchema):
    item_id: int


def _str_or_none(value: str | int | float | bool | None) -> str | None:
    return value if isinstance(value, str) else None


def as_price(value: str | int | float | bool | None) -> int:
    """The lot's asking price as an exact whole number.

    Public because ``batch_submit`` runs its children's literals through the same gate: a batch
    child reaches ``publishing_add`` by reflection and never executes this node, so a validator
    that lives only in ``execute`` guards one of the two paths to the same paid call.

    This port used to be ``float`` — the one money value in the module that was not exact, on the
    call that actually charges for a listing. It is ``int`` rather than ``Decimal`` because the
    marketplace prices lots in whole units: every price it reads back is ``int``, so a fractional
    price here would be published as something other than what was asked for. A wired value that
    is not whole is refused rather than rounded — rounding money silently is how you publish a lot
    at a price nobody chose.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"price must be numeric, got {value!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"price must be numeric, got {value!r}") from exc
    whole = int(number)
    if number != whole:
        raise ValueError(f"price must be a whole number of currency units, got {value!r}")
    if whole <= 0:
        raise ValueError(f"price must be positive, got {value!r}")
    return whole


class RelistNode(BaseNode):
    node_type = "market.relist"
    category = NodeCategory.ACTION
    idempotent = False
    capabilities = MARKET_MUTATE_MONEY
    input_schema = RelistInput
    output_schema = RelistOutput
    required_inputs = ("price", "category_id", "currency", "item_origin")
    batchable = True

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        category_id = ctx.resolve_input("category_id")
        currency_raw = ctx.resolve_input("currency")
        origin_raw = ctx.resolve_input("item_origin")
        try:
            price = as_price(ctx.resolve_input("price"))
        except ValueError as exc:
            raise RunFailed(ctx.run_id, ctx.node.id, str(exc)) from exc
        if isinstance(category_id, bool) or not isinstance(category_id, int):
            raise RunFailed(
                ctx.run_id, ctx.node.id, f"category_id must be an int, got {category_id!r}"
            )
        if not isinstance(currency_raw, str) or not isinstance(origin_raw, str):
            raise RunFailed(ctx.run_id, ctx.node.id, "currency/item_origin must be strings")

        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is None:
            raise NoAvailableAccount(ctx.tenant_id)
        account = await ctx.deps.load_account(ctx.tenant_id, account_ref)

        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first or ctx.step_replay:
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                "relist already published a lot for this step but its result was lost to a crash; "
                "refusing to publish a second paid lot — reconcile the existing lot manually",
            )

        result = await ctx.deps.market.relist(
            account,
            price=price,
            category_id=category_id,
            currency=Currency(currency_raw),
            item_origin=ItemOrigin(origin_raw),
            title=_str_or_none(ctx.resolve_optional("title")),
            description=_str_or_none(ctx.resolve_optional("description")),
        )
        return StepResultDTO(node_id=ctx.node.id, output={"item_id": result.item_id})
