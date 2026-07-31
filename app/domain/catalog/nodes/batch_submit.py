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
from collections.abc import Callable

from pydantic import ValidationError

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import MARKET_MUTATE_MONEY, NodeCategory
from app.domain.catalog.nodes.fast_buy import as_int
from app.domain.catalog.nodes.relist import as_price
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

# A batch child bypasses its node's `execute`, so it bypasses the port validators that live there.
# `RelistNode` refuses a fractional price — the marketplace prices lots in whole units and a
# rounded one publishes a lot at a number nobody chose — while the same FlowSpec run through a
# batch sent `100.5` straight to the paid call. The validators are the node's own, imported rather
# than restated, so the two paths cannot drift into two different contracts.
_Scalar = str | int | float | bool | None
_CHILD_PORT_VALIDATORS: dict[str, dict[str, Callable[[_Scalar], object]]] = {
    "market.bump": {"item_id": lambda v: as_int(v, "item_id")},
    "market.reprice": {"item_id": lambda v: as_int(v, "item_id"), "price": as_price},
    "market.relist": {"price": as_price, "category_id": lambda v: as_int(v, "category_id")},
}


# A child calls the marketplace facade by reflection, so a mis-wired batch surfaces as an ordinary
# Python error at the call — a wrong kwarg name is TypeError, a missing enum member ValueError. Left
# inside the per-item handler those became `{"ok": false, "error": "..."}` for every child, and a
# node that was simply broken read as a marketplace that declined the whole batch. These fail the
# run instead, via runtime.py's documented catch-all, with the traceback intact.
_PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    ValueError,
    KeyError,
    IndexError,
    NameError,
)


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


def _validated_child_inputs(
    child: IRNode, raw: dict[str, object], ctx: RunContext
) -> dict[str, object]:
    """Apply the owning node's own port validators to a batch child's resolved literals."""
    validators = _CHILD_PORT_VALIDATORS.get(child.type, {})
    checked = dict(raw)
    for port, validate in validators.items():
        if port not in checked:
            continue
        value = checked[port]
        if not isinstance(value, str | int | float | bool) and value is not None:
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                f"batch child '{_local_child_id(child)}' port '{port}' is not a scalar",
            )
        try:
            checked[port] = validate(value)
        except ValueError as exc:
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                f"batch child '{_local_child_id(child)}' port '{port}': {exc}",
            ) from exc
    return checked


async def _run_child(
    client: object, child: IRNode, ctx: RunContext
) -> tuple[str, dict[str, object]]:
    child_id = _local_child_id(child)
    mapping = _BATCHABLE_NODE_TO_CALL.get(child.type)
    if mapping is None:
        return child_id, {"ok": False, "error": f"node type '{child.type}' has no batch mapping"}

    # Resolved and validated BEFORE the guard: consuming the key is a claim that the paid call was
    # ATTEMPTED. Both of these steps can refuse the run without going anywhere near the
    # marketplace (a `ref` input, a fractional price), and doing that after check_and_set burned
    # the key — so every later attempt reported "already submitted … reconcile manually" about an
    # item that was never submitted. `fast_buy` already had this order right.
    kwargs = _validated_child_inputs(child, _resolve_child_inputs(child, ctx), ctx)

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
    facade = getattr(client, facade_name)
    method = getattr(facade, method_name)
    try:
        value = await method(**kwargs)
    except ValidationError as exc:
        # Listed before the programming errors because it IS one of them by inheritance
        # (ValidationError subclasses ValueError) and is not one in fact: it means the upstream
        # answered this item with a shape pylzt could not parse — an upstream outcome, per-item.
        return child_id, {"ok": False, "error": repr(exc)}
    except _PROGRAMMING_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 — a child's own MARKETPLACE failure is DATA (per-item
        # outcome), never an exception that fails the whole batch/run (wave-06 decision). The
        # pylzt error tree cannot be named here (only market/adapter.py may import pylzt), so the
        # split is stated the other way round: everything is per-item data EXCEPT the shapes that
        # can only be OUR bug. `repr`, not `str`: pylzt errors carry args rather than
        # pre-formatted text, so `str(Forbidden(...))` is the empty string — the same defect
        # runtime.py already fixed for itself.
        return child_id, {"ok": False, "error": repr(exc)}
    return child_id, {"ok": True, "value": str(value)}


class BatchNode(BaseNode):
    node_type = "logic.batch"
    category = NodeCategory.LOGIC
    idempotent = False
    # batch.submit fans out to arbitrary child nodes, so it inherits their worst case.
    capabilities = MARKET_MUTATE_MONEY
    output_schema = BatchSubmitOutput

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        # No batch-level guard: the money is spent per child, so the key is per child (_run_child).
        # A guard here used to swallow the whole replay — returning {"results": "{}"} and letting
        # the run COMPLETE while the lots it had already published stayed paid for and orphaned.
        children = ctx.node.children or ()
        account_ref = ctx.active_account_id or ctx.node.account_ref
        async with ctx.deps.get_client(ctx.tenant_id, account_ref) as client:
            outcomes = await asyncio.gather(*(_run_child(client, child, ctx) for child in children))

        return StepResultDTO(node_id=ctx.node.id, output={"results": json.dumps(dict(outcomes))})
