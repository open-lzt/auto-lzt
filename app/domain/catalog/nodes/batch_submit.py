"""BatchNode — runs every child node's request concurrently and returns each child's result.

Children come from `IRNode.children` (the wave-06 batch container, authored entirely via canvas
containment — see compiler.py's `_compile_batch_children`), never a hand-typed JSON field.

Implementation note (deviates from the original wave-06 draft, which assumed
`client.execute_batch(methods)` — a real Client method, but one that takes typed `BaseMethod`
request objects whose concrete per-endpoint classes could not be safely confirmed in the time
available; see 00-decisions.md's "unverified" flag on this wave). Instead this node fires every
child concurrently via `asyncio.gather` against the SAME confirmed facade coroutines
`market/adapter.py` already calls (`client.market.managing_bump(...)` etc — the exact call shape
DynamicMethodNode's reflection also resolves), which delivers the real value (all children submit
together, results come back together) without the unverified API surface. Swapping to the real
`execute_batch` RPC is a drop-in change once the per-endpoint `BaseMethod` classes are confirmed.
"""

from __future__ import annotations

import asyncio
import json

from app.core.schema import BaseSchema
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.env_input import resolve_env
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.ir_node import EnvRef, IRNode, LiteralValue, PortRef

# Maps a batchable node's registered type to {facade, method} — the same confirmed call shape
# market/adapter.py already uses (managing_bump/managing_edit/publishing_add).
_BATCHABLE_NODE_TO_CALL: dict[str, tuple[str, str]] = {
    "market.bump": ("market", "managing_bump"),
    "market.reprice": ("market", "managing_edit"),
    "market.relist": ("market", "publishing_add"),
}


class BatchSubmitInput(BaseSchema):
    pass


class BatchSubmitOutput(BaseSchema):
    results: str  # JSON-encoded {child_id: {"ok": bool, "value"?: ..., "error"?: str}}


def _local_child_id(child: IRNode) -> str:
    return child.id.rsplit("::", 1)[-1]


def _resolve_child_inputs(child: IRNode, ctx: RunContext) -> dict[str, object]:
    """Wave-06 scope limit (documented, not a bug): a batch child's inputs must be literals or
    ``{"env": ...}`` — both context-free. A ``ref`` is refused because referencing another top-level
    node's output from inside a batch child would need the child's resolver to see the parent
    chain's ``results`` mapping, which ``RunContext`` doesn't expose to a node (by design — a node
    only ever resolves its own wired ports)."""
    resolved: dict[str, object] = {}
    for port, value in child.inputs.items():
        if isinstance(value, LiteralValue):
            resolved[port] = value.value
        elif isinstance(value, EnvRef):
            # Env inputs are context-free (name + prefix, no parent results), so a batch child can
            # resolve one where it cannot resolve a PortRef. Fails closed like the main resolver.
            resolved[port] = resolve_env(value.name)
        elif isinstance(value, PortRef):
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                f"batch child '{_local_child_id(child)}' port '{port}' references another node — "
                "batch children currently support literal inputs only",
            )
    return resolved


async def _run_child(
    client: object, child: IRNode, ctx: RunContext
) -> tuple[str, dict[str, object]]:
    child_id = _local_child_id(child)
    mapping = _BATCHABLE_NODE_TO_CALL.get(child.type)
    if mapping is None:
        return child_id, {"ok": False, "error": f"node type '{child.type}' has no batch mapping"}

    # The guard is HERE, per child, because the EFFECT is here. A child calls the pylzt client
    # directly, so BumpNode/RelistNode.execute never runs — and neither does the check_and_set they
    # are required to call. A batch-level guard cannot stand in for this: it cannot tell which
    # children got through before the crash, so it either republishes all of them or none.
    first = await ctx.deps.guard.check_and_set(f"{ctx.idempotency_key}:{child_id}")
    if not first:
        # Same trade as relist.py: the effect already happened and its result is lost. Reporting a
        # fake success would poison anything downstream reading this child's value, so say what is
        # actually true and let a human reconcile one item.
        return child_id, {
            "ok": False,
            "error": "already submitted on an earlier attempt; its outcome was lost to a crash — "
            "reconcile this item manually",
        }

    facade_name, method_name = mapping
    kwargs = _resolve_child_inputs(child, ctx)
    facade = getattr(client, facade_name)
    method = getattr(facade, method_name)
    try:
        value = await method(**kwargs)
    except Exception as exc:  # noqa: BLE001 — a child's own failure is DATA (per-item outcome),
        # never an exception that fails the whole batch/run (wave-06 decision).
        return child_id, {"ok": False, "error": str(exc)}
    return child_id, {"ok": True, "value": str(value)}


class BatchNode(BaseNode):
    node_type = "logic.batch"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        # No batch-level guard: the money is spent per child, so the key is per child (_run_child).
        # A guard here used to swallow the whole replay — returning {"results": "{}"} and letting
        # the run COMPLETE while the lots it had already published stayed paid for and orphaned.
        children = ctx.node.children or ()
        account_ref = ctx.active_account_id or ctx.node.account_ref
        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            outcomes = await asyncio.gather(*(_run_child(client, child, ctx) for child in children))

        return StepResultDTO(node_id=ctx.node.id, output={"results": json.dumps(dict(outcomes))})
