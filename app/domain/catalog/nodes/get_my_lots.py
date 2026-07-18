"""GetMyLotsNode — manually pages ``pylzt.Client.market.list_user(user_id=None, page=1..N)``
until exhausted (wave-04 spec; ``list_user`` returns ``ListUserResponse``, page-based, **not** a
``Paginator`` — see ``00-pylzt-compat.md`` CG-6a/CG-6b, so ``.collect()`` does not apply here).

``user_id=None`` resolves to "self" on whichever token the call runs under, so this always needs a
pinned owner account (decision #18) — round-robin would leak another account's lots.
"""

from __future__ import annotations

import json

import structlog

from app.core.schema import BaseSchema
from app.domain.account.errors import NoAvailableAccount
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed

log = structlog.get_logger()

# Guard rail against a misbehaving/looping upstream API — a real seller's lot count is nowhere near
# this; hitting it is a bug, not a legitimate catalog, so it fails loud instead of spinning forever.
_MAX_PAGES = 1000


class GetMyLotsInput(BaseSchema):
    """No wired inputs — the account to list is the pinned owner (``account_ref``/
    ``active_account_id``), never an explicit parameter."""


class GetMyLotsOutput(BaseSchema):
    item_ids: str  # JSON-encoded list[int] — feeds for-each-lot
    count: int


class GetMyLotsNode(BaseNode):
    node_type = "logic.get_my_lots"

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        account_ref = ctx.active_account_id or ctx.node.account_ref
        if account_ref is None:
            raise NoAvailableAccount(ctx.tenant_id)
        account = await ctx.deps.load_account(ctx.tenant_id, account_ref)

        item_ids: list[int] = []
        page = 1
        while True:
            if page > _MAX_PAGES:
                raise RunFailed(
                    ctx.run_id,
                    ctx.node.id,
                    f"list_user did not terminate within {_MAX_PAGES} pages",
                )
            result = await ctx.deps.market.list_my_lots_page(account, page=page)
            item_ids.extend(result.item_ids)
            if not result.has_next_page:
                break
            page += 1

        log.info("get_my_lots.paged", account_id=str(account.id), pages=page, count=len(item_ids))
        return StepResultDTO(
            node_id=ctx.node.id,
            output={"item_ids": json.dumps(item_ids), "count": len(item_ids)},
        )
