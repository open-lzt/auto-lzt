import type { TriggerKind } from "../api/flowClient";

export type NodeVisualCategory = "trigger" | "action" | "logic";

// Which end of a composite template's parameter surface a "templateBoundary" node marks.
export type TemplateBoundaryKind = "input" | "output";

export interface TriggerConfig {
  kind: TriggerKind;
  schedule_cron: string;
  event_type: string;
}

// Mirrors backend NodeSpec.children (app/domain/flow_engine/spec.py) — keep in lockstep.
export interface CanvasChildNodeSpec {
  id: string;
  catalogKey: string;
  values: Record<string, string | number | boolean>;
  children?: CanvasChildNodeSpec[];
}

export interface CanvasNodeData {
  catalogKey: string; // NodeType.key for action/logic (e.g. "market.bump"); trigger kind for trigger nodes
  category: NodeVisualCategory;
  label: string;
  values: Record<string, string | number | boolean>; // AutoForm field values, serialized as literals
  triggerConfig?: TriggerConfig;
  errorMessage?: string; // set by DeployButton on a 400 CompileError naming this node
  children?: CanvasChildNodeSpec[]; // "logic.batch" only — nested step specs, see CanvasChildNodeSpec
  // → NodeSpec.account_ref; null and undefined both mean "no pin".
  accountRef?: string | null;
  /** Render-only, injected by FlowCanvas while drawing — never part of the saved graph. */
  warning?: string;
  boundaryKind?: TemplateBoundaryKind; // "templateBoundary" nodes only — which side of the composite surface this marks
  paramName?: string; // "templateBoundary" nodes only — the declared TemplateParam.name
  [key: string]: unknown;
}
