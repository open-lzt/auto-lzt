// DTOs are hand-mirrored from the Pydantic models they call — keep in lockstep:
//   FlowSpec/NodeSpec/InputSpec  -> app/domain/flow_engine/spec.py
//   CreateTriggerRequest         -> app/api/trigger_routes.py
//   CreateRunRequest             -> app/api/run_routes.py
//   FlowStatusDTO                -> app/api/flow_status_routes.py
//
// src/api must not import from src/canvas — graph serialization lives in canvas/buildFlowSpec.ts.
import { ApiError, request } from "./httpClient";
import type { ParamSpec, ParamValue } from "./paramSpec";

export interface InputSpec {
  literal?: string | number | boolean | null;
  ref?: string | null;
}

export interface NodeSpec {
  id: string;
  type: string;
  inputs: Record<string, InputSpec>;
  account_ref: string | null;
  edges: Record<string, string>;
  on_error: string | null;
  children?: NodeSpec[]; // "logic.batch" only — mirrors backend NodeSpec.children
}

export interface FlowSpec {
  name: string;
  nodes: NodeSpec[];
  entry_node_id: string;
  params?: ParamSpec[];
}

export interface FlowCreatedResponse {
  flow_id: string;
}

export interface FlowCompiledResponse {
  flow_ir_id: string;
  node_count: number;
}

export type TriggerKind = "manual" | "schedule" | "event";

export interface CreateTriggerRequest {
  kind: TriggerKind;
  schedule_cron?: string | null;
  event_type?: string | null;
}

export interface TriggerResponse {
  trigger_id: string;
  kind: TriggerKind;
  schedule_cron: string | null;
  event_type: string | null;
}

export interface CreateRunRequest {
  flow_id: string;
  run_key?: string | null;
  /** Keyed by ParamSpec.key — becomes the `vars` map `{{vars.X}}` reads. */
  params?: Record<string, ParamValue>;
}

export type RunStatus = "pending" | "running" | "completed" | "failed";

export interface RunResponse {
  run_id: string;
  status: RunStatus;
}

export interface FlowStatusResponse {
  running: boolean;
  active_accounts: number;
  last_run_at: string | null;
}

export function createFlow(spec: FlowSpec): Promise<FlowCreatedResponse> {
  return request<FlowCreatedResponse>("/flows/create", { method: "POST", body: JSON.stringify(spec) });
}

export function updateFlow(flowId: string, spec: FlowSpec): Promise<FlowCreatedResponse> {
  return request<FlowCreatedResponse>(`/flows/${flowId}/update`, {
    method: "POST",
    body: JSON.stringify(spec),
  });
}

export function compileFlow(flowId: string): Promise<FlowCompiledResponse> {
  return request<FlowCompiledResponse>(`/flows/${flowId}/compile`, { method: "POST" });
}

export function createTrigger(
  flowId: string,
  body: CreateTriggerRequest,
): Promise<TriggerResponse> {
  return request<TriggerResponse>(`/flows/${flowId}/triggers/create`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createRun(body: CreateRunRequest): Promise<RunResponse> {
  return request<RunResponse>("/runs/create", { method: "POST", body: JSON.stringify(body) });
}

export function fetchFlowStatus(flowId: string): Promise<FlowStatusResponse> {
  return request<FlowStatusResponse>(`/flows/${flowId}/status`);
}

export interface FlowSummary {
  id: string;
  name: string;
}

interface FlowSummaryWire {
  flow_id: string;
  name: string;
}

/** Three states: required=true → key demanded; required=false,open=true → dev hatch, anyone in;
 * required=false,open=false → no key configured, every protected call 401s. */
export interface AuthPosture {
  required: boolean;
  open: boolean;
}

export function authRequired(): Promise<AuthPosture> {
  return request<AuthPosture>("/auth/required");
}

export async function fetchFlows(): Promise<FlowSummary[]> {
  const wire = await request<FlowSummaryWire[]>("/flows/list");
  return wire.map((flow) => ({ id: flow.flow_id, name: flow.name }));
}

export function deleteFlow(flowId: string): Promise<void> {
  return request<void>(`/flows/${flowId}/delete`, { method: "DELETE" });
}

export function renameFlow(flowId: string, name: string): Promise<void> {
  return request<void>(`/flows/${flowId}/rename`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  /** Both null unless `status` is "failed". */
  error: string | null;
  failed_node_id: string | null;
}

export function fetchRunHistory(flowId: string): Promise<RunSummary[]> {
  return request<RunSummary[]>(`/runs/list?flow_id=${encodeURIComponent(flowId)}`);
}

export interface RunTraceStep {
  node_id: string;
  node_type: string;
  args: unknown;
  result: unknown;
  duration_ms: number;
  started_at: string;
  branch_id?: string;
}

export interface RunTrace {
  run_id: string;
  steps: RunTraceStep[];
}

/** Wire fields are inputs/output/iteration_key; reconciled here to args/result/branch_id. */
interface RunTraceEntryWire {
  node_id: string;
  iteration_key: string | null;
  node_type: string;
  inputs: unknown;
  output: unknown;
  duration_ms: number;
  started_at: string;
  completed_at: string;
}

export function fetchRunTrace(runId: string): Promise<RunTrace> {
  return request<RunTraceEntryWire[]>(`/runs/${runId}/trace`).then((entries) => ({
    run_id: runId,
    steps: entries.map((entry) => ({
      node_id: entry.node_id,
      node_type: entry.node_type,
      args: entry.inputs,
      result: entry.output,
      duration_ms: entry.duration_ms,
      started_at: entry.started_at,
      ...(entry.iteration_key ? { branch_id: entry.iteration_key } : {}),
    })),
  }));
}

const EXPORT_SCHEMA_VERSION = 1;

interface FlowExportEnvelope {
  schema_version: number;
  flow: FlowSpec;
}

export function exportFlow(flowId: string): Promise<FlowSpec> {
  return request<FlowExportEnvelope>(`/flows/${flowId}/export`).then((envelope) => envelope.flow);
}

export type ImportStage = "schema" | "compile" | "dry_run";

export interface ImportError {
  node_id: string | null;
  stage: ImportStage;
  message: string;
}

export type ImportResult = { ok: true; flowId: string } | { ok: false; errors: ImportError[] };

interface ImportResultResponse {
  flow_id: string;
  name: string;
}

const IMPORT_STAGE_BY_CODE: Record<string, ImportStage> = {
  "ERR-1012": "schema",
  "ERR-1004": "schema",
  "ERR-1006": "compile",
  "ERR-1013": "dry_run",
};

export function importFlow(spec: FlowSpec): Promise<ImportResult> {
  return request<ImportResultResponse>("/flows/import", {
    method: "POST",
    body: JSON.stringify({ schema_version: EXPORT_SCHEMA_VERSION, flow: spec }),
  }).then(
    (res) => ({ ok: true as const, flowId: res.flow_id }),
    (err: unknown) => {
      if (!(err instanceof ApiError)) throw err;
      const stage = IMPORT_STAGE_BY_CODE[err.code];
      if (!stage) throw err;
      return { ok: false as const, errors: [{ node_id: err.nodeId, stage, message: err.message }] };
    },
  );
}

export interface StepCompletedEvent {
  type: "step_completed";
  event_id: string;
  occurred_at: string;
  run_id: string;
  node_id: string;
  node_type: string;
  iteration_key: string | null;
  duration_ms: number;
}

export interface LogEvent {
  type: "log";
  event_id: string;
  occurred_at: string;
  run_id: string;
  level: string;
  message: string;
}

interface StreamTokenResponse {
  token: string;
  expires_in: number;
}

/** EventSource can't set headers, so X-API-Key is spent once at POST stream-token for a
 * short-lived token carried in the query string instead — hence the extra round trip. */
export async function streamRun(
  runId: string,
  onEvent: (e: StepCompletedEvent | LogEvent) => void,
): Promise<() => void> {
  const { token } = await request<StreamTokenResponse>(`/runs/${runId}/stream-token`, {
    method: "POST",
  });
  const source = new EventSource(
    `/api/runs/${runId}/stream?token=${encodeURIComponent(token)}`,
  );
  source.onmessage = (event: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(event.data) as StepCompletedEvent | LogEvent);
    } catch (err) {
      console.error("streamRun: malformed event frame", err);
    }
  };
  return () => source.close();
}
