"""AutoReplyNode: graceful degrade for the unresolved facade gap (00-decisions.md #19) — never
crashes the flow, always returns a successful, clearly-marked skip."""

from __future__ import annotations

from app.domain.catalog.nodes.auto_reply import AutoReplyNode
from tests.fixtures.flow_fakes import FakeGuard, FakeMarket, build_ctx, build_node


async def test_auto_reply_degrades_gracefully() -> None:
    node = build_node("ar1", "forum.auto_reply", {"conversation_id": 1, "message": "hi"})
    market, guard = FakeMarket(), FakeGuard()
    result = await AutoReplyNode().execute(build_ctx(node, market, guard))
    assert result.output["skipped"] is True
    assert result.output["reason"] == "facade_gap"


async def test_auto_reply_deduplicates_within_guard_window() -> None:
    node = build_node("ar1", "forum.auto_reply", {"conversation_id": 1, "message": "hi"})
    market, guard = FakeMarket(), FakeGuard()
    ctx = build_ctx(node, market, guard)
    await AutoReplyNode().execute(ctx)
    result = await AutoReplyNode().execute(ctx)
    assert result.output["deduplicated"] is True
