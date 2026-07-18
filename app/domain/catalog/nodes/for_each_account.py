"""ForEachAccountNode — the killer-flow's top-level fan-out over the tenant's ACTIVE accounts.

Emits the runtime's fan-out marker with the reserved ``__fanout_port__="account_id"`` — the
interpreter (``app/worker/runtime.py``) recognises that port name and additionally pins
``RunContext.active_account_id`` for the whole iteration subtree (decision #18/#23), so a nested
``get-my-lots``/``for-each-lot``/``bump``/``reprice`` resolves to *this* iteration's owner account
without the compiled ``IRNode.account_ref`` (static) needing to change per item.
"""

from __future__ import annotations

import json

from app.core.schema import BaseSchema
from app.domain.account.model import AccountStatus
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO

_FANOUT_PORT = "account_id"


class ForEachAccountInput(BaseSchema):
    """No wired inputs — the tenant is implicit in ``ctx.tenant_id``."""


class ForEachAccountOutput(BaseSchema):
    count: int


class ForEachAccountNode(BaseNode):
    node_type = "logic.for_each_account"

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        accounts = await ctx.deps.list_accounts(ctx.tenant_id)
        active_ids = [str(a.id) for a in accounts if a.status is AccountStatus.ACTIVE]

        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "__fanout_items__": json.dumps(active_ids),
                "__fanout_port__": _FANOUT_PORT,
                "count": len(active_ids),
            },
        )
