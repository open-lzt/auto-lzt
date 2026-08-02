// Serialization between the canvas graph and the backend Flow-JSON contract, in both directions.
//
// It lives in canvas/ and not in api/ on purpose: this code knows what a React Flow node is, and a
// transport module that knows about the canvas is an inverted dependency — api/ must not import
// anything from here.
import type { Edge, Node } from "@xyflow/react";
import type { FlowSpec, InputSpec, NodeSpec } from "../api/flowClient";
import { isInsideAccountLoop, needsAccount } from "./accountScope";
import type { CanvasChildNodeSpec, CanvasNodeData } from "./canvasTypes";
import type { ParamSpec } from "./paramTypes";

/** Labelled outgoing control-flow ports per node type (app/domain/flow_engine/ir_node.py docstring:
 * "next" for linear flow, "true"/"false" for condition, "body"/"after" for the fan-out nodes).
 * Not part of the catalog response — the catalog only carries *input* schema. */
// Keys are the node's FULL catalog key (`node_type` on the backend: "logic.condition", not
// "condition") because that is what `outputPortsFor` is called with. They were written unprefixed,
// so every lookup missed and every branching node fell back to a single "next" handle: the second
// port was never rendered, and React Flow then dropped any saved edge bound to it — a for-each's
// body edge and a condition's false edge simply vanished from a flow the user had already built.
// The graph was drawn wrong, not merely styled wrong, and it only announced itself as a console
// warning. Keep in sync with `node_type` in app/domain/catalog/nodes/.
const OUTPUT_PORTS: Record<string, string[]> = {
  "logic.condition": ["true", "false"],
  "logic.for_each_lot": ["body", "after"],
  "logic.for_each_account": ["body", "after"],
};

export function outputPortsFor(catalogKey: string): string[] {
  return OUTPUT_PORTS[catalogKey] ?? ["next"];
}

// AutoForm keeps number fields as the raw typed string (so a trailing "." doesn't get eaten
// mid-edit) — coerce a complete numeric string to a JS number right before it leaves the client.
export function coerceNumericString(value: string | number | boolean): string | number | boolean {
  if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value)) {
    return Number(value);
  }
  return value;
}

export class FlowBuildError extends Error {
  readonly nodeId: string | null;
  constructor(message: string, nodeId: string | null = null) {
    super(message);
    this.nodeId = nodeId;
  }
}

export const BATCH_CATALOG_KEY = "logic.batch";

/** Turns a canvas value map into wire inputs. Empty strings are dropped rather than sent as ""
 * — the backend would take them as a provided value and fail validation on a number field. */
export function buildInputs(
  values: Record<string, string | number | boolean>,
): Record<string, InputSpec> {
  const inputs: Record<string, InputSpec> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value === "" || value === undefined) continue;
    inputs[key] = { literal: coerceNumericString(value) };
  }
  return inputs;
}

// Nested children carry no canvas position/edges of their own (they're not draggable nodes on the
// graph) — only type+inputs, recursively, matching backend NodeSpec.children's own recursive shape.
export function buildChildNodeSpec(child: CanvasChildNodeSpec): NodeSpec {
  return {
    id: child.id,
    type: child.catalogKey,
    inputs: buildInputs(child.values),
    // A batch child has no row of its own in the inspector, so there is nowhere to pin an account
    // to it. The runtime prefers active_account_id anyway: wrap the batch in for_each_account.
    account_ref: null,
    edges: {},
    on_error: null,
    children:
      child.catalogKey === BATCH_CATALOG_KEY && child.children?.length
        ? child.children.map(buildChildNodeSpec)
        : undefined,
  };
}

/** Pulls the literal inputs of a NodeSpec back into the flat value map AutoForm edits.
 * `ref` inputs have no canvas representation and are dropped — the canvas never produces one. */
export function literalValues(
  inputs: Record<string, InputSpec>,
): Record<string, string | number | boolean> {
  const values: Record<string, string | number | boolean> = {};
  for (const [key, input] of Object.entries(inputs)) {
    const literal = input.literal;
    if (typeof literal === "string" || typeof literal === "number" || typeof literal === "boolean") {
      values[key] = literal;
    }
  }
  return values;
}

/** The inverse of buildChildNodeSpec, and it must stay its mirror: reopening a saved flow rebuilds
 * the canvas from the wire spec, so anything this drops is silently gone the next time the operator
 * publishes over the same flow. Batch children were exactly that bug. */
export function childNodeSpecToCanvas(spec: NodeSpec): CanvasChildNodeSpec {
  return {
    id: spec.id,
    catalogKey: spec.type,
    values: literalValues(spec.inputs),
    children: spec.children?.length ? spec.children.map(childNodeSpecToCanvas) : undefined,
  };
}

/** Serializes the canvas graph into the exact Flow-JSON shape POST /flows expects. The Trigger
 * node is canvas-only UI (triggers are attached via a separate POST .../triggers call after
 * compile, not as a graph node) — its outgoing edge marks the entry node instead. */
export function buildFlowSpec(
  name: string,
  nodes: Node<CanvasNodeData>[],
  edges: Edge[],
  params: ParamSpec[] = [],
): { spec: FlowSpec; triggerData: CanvasNodeData } {
  const triggerNodes = nodes.filter((n) => n.data.category === "trigger");
  if (triggerNodes.length === 0) {
    throw new FlowBuildError("добавьте на холст триггер-блок — без него флоу некому запускать");
  }
  // Refused rather than resolved: picking one silently is how an operator publishes the schedule of
  // a trigger they thought they had replaced. The extra node is invisible in the deployed flow.
  if (triggerNodes.length > 1) {
    throw new FlowBuildError(
      "на холсте больше одного триггера — оставьте один, остальные удалите",
      triggerNodes[1].id,
    );
  }
  const triggerNode = triggerNodes[0];

  const domainNodes = nodes.filter((n) => n.data.category !== "trigger");
  if (domainNodes.length === 0) {
    throw new FlowBuildError("добавьте хотя бы один action/logic блок");
  }

  // A schedule fires with nobody at the keyboard, so there is no form to fill a required param in:
  // resolve_params would raise ParamValidationError on every tick and the operator would only find
  // out from the run history. Refused here instead, where the param can still be given a default.
  if (triggerNode.data.triggerConfig?.kind === "schedule") {
    const unfillable = params.find((p) => p.required && p.default == null);
    if (unfillable) {
      throw new FlowBuildError(
        `параметр «${unfillable.label}» обязателен и не имеет значения по умолчанию — флоу по расписанию заполнить его некому: задайте значение по умолчанию или снимите обязательность`,
        triggerNode.id,
      );
    }
  }

  const entryEdge = edges.find((e) => e.source === triggerNode.id);
  if (!entryEdge) {
    throw new FlowBuildError("соедините триггер с первым блоком флоу", triggerNode.id);
  }

  const nodeSpecs: NodeSpec[] = domainNodes.map((node) => {
    // Refused at publish time, not at open time: a flow saved before pinning existed may hold such
    // a node, and blocking the editor would mean its owner cannot save ANY edit until they deal
    // with the account. The node is marked on the canvas as soon as it is drawn, so this is the
    // second warning, not the first.
    if (needsAccount(node.data.catalogKey) && !node.data.accountRef) {
      if (!isInsideAccountLoop(node.id, nodes, edges)) {
        throw new FlowBuildError(
          `узлу «${node.data.label}» нужен аккаунт — выберите его в инспекторе или оберните узел в цикл по аккаунтам`,
          node.id,
        );
      }
    }

    const outEdges: Record<string, string> = {};
    for (const edge of edges) {
      if (edge.source !== node.id) continue;
      const port = edge.sourceHandle ?? "next";
      outEdges[port] = edge.target;
    }
    return {
      id: node.id,
      type: node.data.catalogKey,
      inputs: buildInputs(node.data.values),
      account_ref: node.data.accountRef ?? null,
      edges: outEdges,
      on_error: null,
      children:
        node.data.catalogKey === BATCH_CATALOG_KEY && node.data.children?.length
          ? node.data.children.map(buildChildNodeSpec)
          : undefined,
    };
  });

  return {
    spec: { name, nodes: nodeSpecs, entry_node_id: entryEdge.target, params },
    triggerData: triggerNode.data,
  };
}
