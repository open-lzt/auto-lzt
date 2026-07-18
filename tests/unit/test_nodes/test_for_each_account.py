"""ForEachAccountNode: fans out over ACTIVE accounts only, port name pins active_account_id."""

from __future__ import annotations

import json

from app.domain.account.model import AccountStatus
from app.domain.catalog.nodes.for_each_account import ForEachAccountNode
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_account, build_ctx, build_node


async def test_for_each_account_filters_active_only() -> None:
    active = build_account()
    excluded = build_account()
    excluded = excluded.__class__(
        id=excluded.id,
        tenant_id=excluded.tenant_id,
        encrypted_token=excluded.encrypted_token,
        created_at=excluded.created_at,
        status=AccountStatus.EXCLUDED,
    )
    node = build_node("fa1", "logic.for_each_account", {})

    async def list_accounts(tenant_id: object) -> list[object]:
        return [active, excluded]

    ctx = build_ctx(node, FakeMarket(), FakeGuard(), list_accounts=list_accounts)
    result = await ForEachAccountNode().execute(ctx)

    ids = json.loads(result.output["__fanout_items__"])
    assert ids == [str(active.id)]
    assert result.output["__fanout_port__"] == "account_id"
    assert result.output["count"] == 1
