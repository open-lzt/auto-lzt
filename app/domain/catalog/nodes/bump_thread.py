"""BumpThreadNode — the forum-side counterpart of ``market.bump``.

Wraps ``MarketAdapter.bump_thread`` / ``pylzt.Client.forum.threads_bump``. Unlike the lot bump
there is no pooled fallback: a thread belongs to the account that posted it, so the credential
is never the round-robin's to choose (see ``MarketService.bump_thread``).
"""

from __future__ import annotations

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_MUTATE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class BumpThreadInput(BaseSchema):
    thread_id: int = Field(
        title="Тема",
        description="ID темы на форуме.",
        json_schema_extra={"x-ui": {"widget": "number"}},
        gt=0,
    )


class BumpThreadOutput(BaseSchema):
    thread_id: int
    bumped_at: str  # ISO-8601, UTC


def _as_int(value: str | int | float | bool | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"thread_id must be an int, got {value!r}")
    return int(value)


class BumpThreadNode(BaseNode):
    node_type = "forum.bump_thread"
    category = NodeCategory.ACTION
    idempotent = True
    # MARKET_MUTATE without MONEY: bumping a thread changes listing order, it never moves funds.
    capabilities = MARKET_MUTATE
    input_schema = BumpThreadInput
    output_schema = BumpThreadOutput
    required_inputs = ("thread_id",)

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        thread_id = _as_int(ctx.resolve_input("thread_id"))
        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            return StepResultDTO(
                node_id=ctx.node.id, output={"thread_id": thread_id, "deduplicated": True}
            )

        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is None:
            raise ValueError("forum.bump_thread needs an account: a thread is bumped by its owner")
        account = await ctx.deps.load_account(ctx.tenant_id, account_ref)
        result = await ctx.deps.market.bump_thread(thread_id, account)

        return StepResultDTO(
            node_id=ctx.node.id,
            output={"thread_id": result.thread_id, "bumped_at": result.bumped_at.isoformat()},
        )
