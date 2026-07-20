"""IR compiler: a validated Flow → immutable FlowIR, or CompileError before any runtime touches it.

Checks: unique node ids, entry exists, every input wired (literal or resolvable ref), required
inputs present (from the node's ``required_inputs`` contract), edge/on_error targets exist, and the
graph is acyclic. ``{{vars.x}}`` literals are rewritten to a PortRef against the reserved ``vars``
pseudo-node. The node registry is injected (DI) so this domain module never imports the worker.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

from app.domain.account.model import AccountId
from app.domain.flow_engine.base_node import BaseNode
from app.domain.flow_engine.errors import (
    CompileError,
    CompositeCycleError,
    CompositeDepthExceeded,
    UnknownTemplate,
)
from app.domain.flow_engine.ir_node import (
    VARS_NODE_ID,
    EnvRef,
    IRNode,
    LiteralValue,
    PortRef,
    StopCondition,
)
from app.domain.flow_engine.model import Flow, FlowIR, FlowIrId, FlowTemplate
from app.domain.flow_engine.path import parse_path
from app.domain.flow_engine.spec import InputSpec, NodeSpec

_VARS_TEMPLATE = re.compile(r"^\{\{\s*vars\.(\w+)\s*\}\}$")
_PARAM_TEMPLATE = re.compile(r"^\{\{\s*param\.(\w+)\s*\}\}$")
_REF_HEAD = re.compile(r"^(?P<node_id>[\w:]+)\.(?P<port>\w+)(?P<path>.*)$")  # "node_id.port[path]"
# node_id allows ':' for wave-05's compiler-internal `<caller>::<inner>` namespacing — user-supplied
# NodeSpec.id is separately gated to plain \w+ at the FlowSpec trust boundary (D1-4), so a ':' can
# only ever appear here in an already-namespaced, compiler-generated ref, never from raw user input.
_CUSTOM_PREFIX = "custom."
_MAX_COMPOSITE_DEPTH = 10

TemplateLookup = Callable[[UUID], "FlowTemplate | None"]


_DEFAULT_BATCH_MAX_CHILDREN = 50


def compile_flow(
    flow: Flow,
    registry: Mapping[str, type[BaseNode]],
    template_lookup: TemplateLookup | None = None,
    batch_max_children: int = _DEFAULT_BATCH_MAX_CHILDREN,
) -> FlowIR:
    nodes, entry_node_id = expand_composites(
        list(flow.spec.nodes), flow.spec.entry_node_id, template_lookup
    )
    by_id: dict[str, NodeSpec] = {}
    for node in nodes:
        if node.id in by_id:
            raise CompileError("duplicate node id", node.id)
        by_id[node.id] = node

    if entry_node_id not in by_id:
        raise CompileError("entry node not found", entry_node_id)

    ir_nodes: list[IRNode] = []
    for node in nodes:
        node_cls = registry.get(node.type)
        if node_cls is None:
            raise CompileError(f"unknown node type '{node.type}'", node.id)

        inputs = {port: _resolve_input(spec, by_id, node.id) for port, spec in node.inputs.items()}
        for required in node_cls.required_inputs:
            if required not in inputs:
                raise CompileError(f"missing required input '{required}'", node.id)
        node_cls.validate_compile(node.id, inputs)

        for label, target in node.edges.items():
            if target not in by_id:
                raise CompileError(f"dangling edge '{label}' -> '{target}'", node.id)
        if node.on_error is not None and node.on_error not in by_id:
            raise CompileError(f"dangling on_error -> '{node.on_error}'", node.id)

        stop_condition = _compile_stop_condition(node, by_id)

        ir_nodes.append(
            IRNode(
                id=node.id,
                type=node.type,
                inputs=inputs,
                account_ref=AccountId(node.account_ref) if node.account_ref else None,
                edges=dict(node.edges),
                on_error=node.on_error,
                timeout_s=node.timeout_s,
                stop_condition=stop_condition,
                children=_compile_batch_children(node, by_id, registry, batch_max_children),
            )
        )

    _assert_acyclic(ir_nodes)
    return FlowIR(
        id=FlowIrId(uuid4()),
        flow_id=flow.id,
        version=flow.version,
        nodes=tuple(ir_nodes),
        entry_node_id=entry_node_id,
    )


def _is_composite_call(node: NodeSpec) -> bool:
    return node.type.startswith(_CUSTOM_PREFIX)


def _template_id_of(node: NodeSpec) -> UUID:
    raw = node.type[len(_CUSTOM_PREFIX) :]
    try:
        return UUID(raw)
    except ValueError as exc:
        raise CompileError(f"malformed composite type '{node.type}'", node.id) from exc


def _rewrite_param_ref(
    ispec: InputSpec, caller_inputs: Mapping[str, InputSpec], namespace: Callable[[str], str]
) -> InputSpec:
    """Inside a template's own node inputs: `{{param.NAME}}` substitutes for the calling node's
    actual wired input; any other ref to a template-internal sibling gets namespaced (`vars` is
    the one reserved node id that is never namespaced — it always refers to the outer flow's own
    variables, per the existing `{{vars.x}}` convention)."""
    if ispec.ref is not None:
        match = _REF_HEAD.match(ispec.ref)
        if match is None or match["node_id"] == VARS_NODE_ID:
            return ispec
        return InputSpec(ref=f"{namespace(match['node_id'])}.{match['port']}{match['path']}")

    if isinstance(ispec.literal, str):
        match = _PARAM_TEMPLATE.match(ispec.literal)
        if match:
            param_name = match.group(1)
            if param_name not in caller_inputs:
                raise CompileError(f"composite param '{param_name}' not wired by caller")
            return caller_inputs[param_name]
    return ispec


def _redirect_input_ref(ispec: InputSpec, output_redirect: Mapping[str, str]) -> InputSpec:
    """A plain (non-composite) sibling's ref pointing at an inlined composite call's declared
    output param gets redirected to the actual internal node.port that produces it."""
    if ispec.ref is None:
        return ispec
    match = _REF_HEAD.match(ispec.ref)
    if match is None:
        return ispec
    key = f"{match['node_id']}.{match['port']}"
    if key not in output_redirect:
        return ispec
    return InputSpec(ref=f"{output_redirect[key]}{match['path']}")


def _redirect_node(
    node: NodeSpec, entry_redirect: Mapping[str, str], output_redirect: Mapping[str, str]
) -> NodeSpec:
    return NodeSpec(
        id=node.id,
        type=node.type,
        inputs={
            port: _redirect_input_ref(ispec, output_redirect) for port, ispec in node.inputs.items()
        },
        account_ref=node.account_ref,
        edges={label: entry_redirect.get(target, target) for label, target in node.edges.items()},
        on_error=(entry_redirect.get(node.on_error, node.on_error) if node.on_error else None),
        timeout_s=node.timeout_s,
        stop_condition=node.stop_condition,
        children=node.children,
    )


def expand_composites(
    nodes: list[NodeSpec],
    entry_node_id: str,
    template_lookup: TemplateLookup | None,
    *,
    visited: tuple[UUID, ...] = (),
    depth: int = 0,
) -> tuple[list[NodeSpec], str]:
    """Recursively inlines every `custom.<template_id>` NodeSpec (wave-05) into a fully-flat,
    fully-resolved node list — no `custom.*` type and no dangling cross-reference remains once
    this returns, so the rest of `compile_flow`/the runtime never learns composites exist
    (decision #3). Returns the expanded list plus the (possibly redirected) entry node id."""
    if depth > _MAX_COMPOSITE_DEPTH:
        raise CompositeDepthExceeded(depth, _MAX_COMPOSITE_DEPTH)

    entry_redirect: dict[str, str] = {}
    output_redirect: dict[str, str] = {}
    plain_nodes: list[NodeSpec] = []
    inlined_nodes: list[NodeSpec] = []

    for node in nodes:
        if not _is_composite_call(node):
            plain_nodes.append(node)
            continue

        template_id = _template_id_of(node)
        if template_id in visited:
            chain = (*(str(v) for v in visited), str(template_id))
            raise CompositeCycleError(chain)
        if template_lookup is None:
            raise UnknownTemplate(node.type, node.id)
        template = template_lookup(template_id)
        if template is None:
            raise UnknownTemplate(str(template_id), node.id)

        inner_nodes, inner_entry_id = expand_composites(
            list(template.nodes),
            template.entry_node_id,
            template_lookup,
            visited=(*visited, template_id),
            depth=depth + 1,
        )

        def namespace(inner_id: str, _prefix: str = node.id) -> str:
            return f"{_prefix}::{inner_id}"

        for inner in inner_nodes:
            # Splice the calling node's own outgoing edges/on_error onto every internal leaf (a
            # namespaced node with no edges of its own) — that's the template's exit boundary;
            # nodes that already continue internally (e.g. b1 -> b2) are left untouched. Spliced
            # values are the CALLER's own edge targets (outer-scope ids), so they must NOT be
            # namespaced by this level's `namespace()`.
            is_leaf = not inner.edges
            edges = (
                dict(node.edges)
                if is_leaf
                else {label: namespace(target) for label, target in inner.edges.items()}
            )
            on_error: str | None
            if inner.on_error is not None:
                on_error = namespace(inner.on_error)
            else:
                on_error = node.on_error if is_leaf else None
            # model_construct, not NodeSpec(...): the `::` namespace separator is a compiler-
            # internal composition of already-validated parts, not untrusted input — NodeSpec.id's
            # `^\w+$` field_validator gates the FlowSpec trust boundary (D1-4), which this isn't.
            inlined_nodes.append(
                NodeSpec.model_construct(
                    id=namespace(inner.id),
                    type=inner.type,
                    inputs={
                        port: _rewrite_param_ref(ispec, node.inputs, namespace)
                        for port, ispec in inner.inputs.items()
                    },
                    account_ref=inner.account_ref,
                    edges=edges,
                    on_error=on_error,
                )
            )

        entry_redirect[node.id] = namespace(inner_entry_id)
        for out_param in template.outputs:
            if out_param.output_port is None:
                continue
            inner_node_id, _, port = out_param.output_port.partition(".")
            output_redirect[f"{node.id}.{out_param.name}"] = f"{namespace(inner_node_id)}.{port}"

    rewritten_plain = [_redirect_node(n, entry_redirect, output_redirect) for n in plain_nodes]
    final_entry = entry_redirect.get(entry_node_id, entry_node_id)
    return [*rewritten_plain, *inlined_nodes], final_entry


def _compile_stop_condition(node: NodeSpec, by_id: Mapping[str, NodeSpec]) -> StopCondition | None:
    sc = node.stop_condition
    if sc is None:
        return None
    if sc.action == "goto" and sc.goto_node_id not in by_id:
        raise CompileError(f"stop_condition goto -> unknown node '{sc.goto_node_id}'", node.id)
    return StopCondition(
        output_key=sc.output_key, equals=sc.equals, action=sc.action, goto_node_id=sc.goto_node_id
    )


def _compile_batch_children(
    node: NodeSpec,
    by_id: Mapping[str, NodeSpec],
    registry: Mapping[str, type[BaseNode]],
    batch_max_children: int,
) -> tuple[IRNode, ...] | None:
    """Wave-06 batch container: each child is a normal, fully-typed leaf — no edges/on_error of
    its own, and its registered node class must opt in via ``batchable = True``. Compiled into
    namespaced IRNodes (`<batch_node_id>::<child_id>`, same scheme as wave-05's composite
    inlining) stored on the parent's `IRNode.children`, never spliced into the top-level walk."""
    if node.children is None:
        return None
    if len(node.children) > batch_max_children:
        raise CompileError(
            f"batch has {len(node.children)} children, exceeds cap of {batch_max_children}",
            node.id,
        )

    compiled: list[IRNode] = []
    seen_child_ids: set[str] = set()
    for child in node.children:
        if child.id in seen_child_ids:
            raise CompileError(f"duplicate batch child id '{child.id}'", node.id)
        seen_child_ids.add(child.id)

        if child.edges or child.on_error is not None:
            raise CompileError(
                f"batch child '{child.id}' must not have edges/on_error (data leaf only)", node.id
            )
        child_cls = registry.get(child.type)
        if child_cls is None:
            raise CompileError(f"unknown node type '{child.type}'", child.id)
        if not child_cls.batchable:
            raise CompileError(
                f"node type '{child.type}' (child '{child.id}') is not batchable", node.id
            )

        child_inputs = {
            port: _resolve_input(spec, by_id, child.id) for port, spec in child.inputs.items()
        }
        for required in child_cls.required_inputs:
            if required not in child_inputs:
                raise CompileError(f"missing required input '{required}'", child.id)
        child_cls.validate_compile(child.id, child_inputs)

        compiled.append(
            IRNode(
                id=f"{node.id}::{child.id}",
                type=child.type,
                inputs=child_inputs,
                account_ref=AccountId(child.account_ref) if child.account_ref else None,
                edges={},
                on_error=None,
            )
        )
    return tuple(compiled)


def _resolve_input(
    spec: InputSpec, by_id: Mapping[str, NodeSpec], node_id: str
) -> PortRef | LiteralValue | EnvRef:
    if spec.env is not None:
        # The whole point of "read on each access": the IR carries the NAME, never the value.
        return EnvRef(name=spec.env)
    if spec.ref is not None:
        match = _REF_HEAD.match(spec.ref)
        if match is None:
            raise CompileError(f"malformed ref '{spec.ref}' (want 'node_id.port[path]')", node_id)
        ref_node, ref_port, path_raw = match["node_id"], match["port"], match["path"]
        if ref_node not in by_id:
            raise CompileError(f"ref to unknown node '{ref_node}'", node_id)
        try:
            path = parse_path(path_raw)
        except ValueError as exc:
            raise CompileError(f"malformed path in ref '{spec.ref}': {exc}", node_id) from exc
        return PortRef(node_id=ref_node, port=ref_port, path=path)

    literal = spec.literal
    if isinstance(literal, str):
        match = _VARS_TEMPLATE.match(literal)
        if match:
            return PortRef(node_id=VARS_NODE_ID, port=match.group(1))
    # InputSpec's validator guarantees literal is set when ref and env are both None.
    assert literal is not None
    return LiteralValue(value=literal)


def _assert_acyclic(nodes: list[IRNode]) -> None:
    """DFS with three colours. Edges to the reserved ``vars`` pseudo-node are data refs, not control
    flow, and are ignored here. A node's own edge to its own id (``WaitUntilNode``'s self-loop,
    wave-02) is a deliberate single-node repeat, not a cross-node cycle — runtime.py's self-loop
    protocol gives each revisit a fresh iteration_key, so it is excluded here too."""

    def _targets(node: IRNode) -> list[str]:
        raw = (*node.edges.values(), *((node.on_error,) if node.on_error else ()))
        return [t for t in raw if t not in (VARS_NODE_ID, node.id)]

    adjacency = {n.id: _targets(n) for n in nodes}
    visiting, done = set[str](), set[str]()

    def visit(node_id: str) -> None:
        if node_id in done:
            return
        if node_id in visiting:
            raise CompileError("cycle detected", node_id)
        visiting.add(node_id)
        for target in adjacency.get(node_id, ()):
            visit(target)
        visiting.discard(node_id)
        done.add(node_id)

    for node in nodes:
        visit(node.id)
