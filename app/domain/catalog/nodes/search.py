"""SearchNode — the buyer-side counterpart of ``get-my-lots``.

``get-my-lots`` lists what you own; this lists what you could buy. Both emit the same shape — a
JSON array of item ids — so both feed the existing ``take -> for-each-lot`` fan-out instead of
needing a loop of their own. That is the whole reason the output is an id list and not a rich
object: ``for-each-lot`` fans out on ``item_id``, and a node that returned lot structs would have
no way to reach it.

The price ceiling lives here, not on the buy node: ``pmax`` filters server-side, so a lot above it
never enters the list that ``fast-buy`` later consumes.
"""

from __future__ import annotations

import json
import statistics

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_READ, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.market.categories import SearchableCategory


class SearchInput(BaseSchema):
    max_price: float = Field(
        gt=0,
        title="Цена до",
        description="Потолок цены лота. Фильтрует маркет, а не мы — дороже сюда не попадёт.",
        json_schema_extra={"x-ui": {"widget": "number"}},
    )
    category: SearchableCategory = Field(
        default=SearchableCategory.STEAM,
        title="Категория",
        description="Раздел маркета, в котором искать.",
        json_schema_extra={"x-ui": {"widget": "select"}},
    )


class SearchOutput(BaseSchema):
    item_ids: str  # JSON-encoded list[int] — feeds take / for-each-lot
    count: int
    cheapest_price: float
    median_price: float


def _as_float(value: str | int | float | bool | None, port: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{port} must be a number, got {value!r}")
    return float(value)


def _as_category(value: str | int | float | bool | None) -> SearchableCategory:
    """Unwired port means Steam — the historical behaviour, so an existing flow keeps working.

    ``input_schema`` is catalog metadata only (it is rendered to JSON Schema for the picker, never
    validated against a resolved port), so this is the one place an unknown slug can be rejected
    before it reaches the facade.
    """
    if value is None:
        return SearchableCategory.STEAM
    if not isinstance(value, str):
        raise ValueError(f"category must be a string, got {value!r}")
    try:
        return SearchableCategory(value)
    except ValueError as exc:
        known = ", ".join(sorted(c.value for c in SearchableCategory))
        raise ValueError(f"unknown category {value!r}; searchable categories: {known}") from exc


class SearchNode(BaseNode):
    node_type = "market.search"
    category = NodeCategory.LOGIC
    idempotent = True
    capabilities = MARKET_READ
    input_schema = SearchInput
    output_schema = SearchOutput
    required_inputs = ("max_price",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        max_price = _as_float(ctx.resolve_input("max_price"), "max_price")
        category = _as_category(ctx.resolve_optional("category"))

        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is not None:
            account = await ctx.deps.load_account(ctx.tenant_id, account_ref)
            result = await ctx.deps.market.search_category(
                account, category=category, pmax=max_price
            )
        else:
            result = await ctx.deps.market.search_category_via_pool(
                ctx.tenant_id, category=category, pmax=max_price
            )

        prices = [hit.price for hit in result.hits]
        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "item_ids": json.dumps([hit.item_id for hit in result.hits]),
                "count": len(result.hits),
                "cheapest_price": min(prices) if prices else 0.0,
                "median_price": float(statistics.median(prices)) if prices else 0.0,
            },
        )
