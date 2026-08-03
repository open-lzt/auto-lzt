// Lives in canvas/, not api/: api/ must not import anything that knows about React Flow nodes.
import type { Edge, Node } from "@xyflow/react";
import type { FlowSpec, InputSpec, NodeSpec } from "../api/flowClient";
import { isInsideAccountLoop, needsAccount } from "./accountScope";
import type { CanvasChildNodeSpec, CanvasNodeData } from "./canvasTypes";
import type { ParamSpec } from "./paramTypes";

// Keys are the node's FULL catalog key ("logic.condition", not "condition") — must stay in sync
// with `node_type` in app/domain/catalog/nodes/, or branching nodes silently lose their 2nd port.
const OUTPUT_PORTS: Record<string, string[]> = {
  "logic.condition": ["true", "false"],
  "logic.for_each_lot": ["body", "after"],
  "logic.for_each_account": ["body", "after"],
};

export function outputPortsFor(catalogKey: string): string[] {
  return OUTPUT_PORTS[catalogKey] ?? ["next"];
}

// AutoForm keeps number fields as raw strings mid-edit (so a trailing "." isn't eaten); coerce here.
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

// Empty strings are dropped, not sent as "" — the backend would fail number-field validation on it.
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

export function buildChildNodeSpec(child: CanvasChildNodeSpec): NodeSpec {
  return {
    id: child.id,
    type: child.catalogKey,
    inputs: buildInputs(child.values),
    // Batch children have no inspector row to pin an account to; wrap the batch in for_each_account.
    account_ref: null,
    edges: {},
    on_error: null,
    children:
      child.catalogKey === BATCH_CATALOG_KEY && child.children?.length
        ? child.children.map(buildChildNodeSpec)
        : undefined,
  };
}

// `ref` inputs have no canvas representation and are dropped here.
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

// Must stay the mirror of buildChildNodeSpec — whatever it drops vanishes on the next republish.
export function childNodeSpecToCanvas(spec: NodeSpec): CanvasChildNodeSpec {
  return {
    id: spec.id,
    catalogKey: spec.type,
    values: literalValues(spec.inputs),
    children: spec.children?.length ? spec.children.map(childNodeSpecToCanvas) : undefined,
  };
}

// The Trigger node is canvas-only UI (attached via a separate POST .../triggers call, not a graph
// node) — its outgoing edge marks the entry node instead.
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

  // A schedule fires unattended: a required param with no default would fail resolve_params on
  // every tick with no one to see it.
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
