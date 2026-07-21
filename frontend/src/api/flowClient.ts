// Typed client for the lzt-flow backend. DTOs are hand-mirrored from the Pydantic models they
// call — no shared codegen at this 2-service scale (wave-06 §Risks accepts the drift risk; an
// e2e test is the guard rail). Every shape here must match its backend counterpart byte-for-byte:
//   FlowSpec/NodeSpec/InputSpec  -> app/domain/flow_engine/spec.py
//   CreateTriggerRequest         -> app/api/trigger_routes.py
//   CreateRunRequest             -> app/api/run_routes.py
//   FlowStatusDTO                -> app/api/flow_status_routes.py
//   CatalogNodeResponse          -> app/api/catalog_routes.py
import type { Edge, Node } from "@xyflow/react";
import type { CanvasChildNodeSpec, CanvasNodeData } from "../canvas/canvasTypes";
import type { ParamSpec } from "../canvas/paramTypes";

export type NodeCategory = "action" | "logic" | "trigger";

/**
 * The ONE ui-hint vocabulary, shared by every renderer of a node schema.
 *
 * Both halves of this union are emitted by the backend today, and it is worth knowing why they look
 * like two lists. `text`/`number`/`bool`/`select`/`lot_ref`/`secret`/`account_ref` say what a field
 * IS — that vocabulary predates the canvas and is what the Telegram bot's form renderer parses chat
 * input with (`UiKind` in `app/bot/render/schema_form.py`). `slider`/`textarea`/`radio`/
 * `multiselect`/`datetime` say which CONTROL to draw when the JSON-schema type alone is too weak.
 *
 * They live in one union because they travel in one field. Listing only the canvas's half would be
 * a type that lies: `lot_ref` reaches this code at runtime, and an exhaustive `switch` written
 * against a narrower union would be wrong the first time it ran.
 *
 * Neither consumer implements the whole vocabulary, and neither has to — an unrecognised widget
 * falls through to the default control on both sides rather than throwing.
 */
export type UiWidget =
  | "slider"
  | "textarea"
  | "radio"
  | "multiselect"
  | "datetime"
  | "text"
  | "number"
  | "bool"
  | "select"
  | "lot_ref"
  | "secret"
  | "account_ref"
  | "category_picker";

export interface JsonSchemaUi {
  widget?: UiWidget;
  step?: number;
  unit?: string;
  /** Human captions for an enum's values. JSON Schema has nowhere to put them, so a cron-valued
   * enum would render its raw expression instead of «Каждые 30 минут». This is the ONE way a
   * choice gets a label — do not grow a second. */
  options?: { value: string; label: string }[];
  /** Explicit position in the form; absent means declaration order. Exists because Pydantic
   * emits INHERITED fields first, so a schedule declared on a shared base would open every
   * preset with «Как часто» — asking when before who. */
  order?: number;
}

export interface JsonSchema {
  type?: string;
  title?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: (string | number)[];
  anyOf?: JsonSchema[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  "x-ui"?: JsonSchemaUi;
  [key: string]: unknown;
}

export interface CatalogNode {
  key: string;
  category: NodeCategory;
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  idempotent: boolean;
  /** What the node can do — the same vocabulary the module validator filters on. Rendered as a
   * badge so an operator can see that a node spends money before wiring it, not after. */
  capabilities: NodeCapability[];
}

export type NodeCapability =
  | "market.read"
  | "market.mutate"
  | "network.egress"
  | "reflective"
  | "money"
  | "pure";

/** GET /catalog/list's envelope. The version exists so this client refuses to render a shape it
 * does not understand rather than guessing at it. */
export interface CatalogListResponse {
  schema_version: number;
  nodes: CatalogNode[];
}

/** Bump in lockstep with CATALOG_SCHEMA_VERSION in app/api/catalog_routes.py. */
export const CATALOG_SCHEMA_VERSION = 1;

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
  children?: NodeSpec[]; // "logic.batch" only, mirrors backend NodeSpec.children
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

interface ErrorEnvelope {
  code: string;
  message: string;
  request_id: string;
}

/** Raised on a non-2xx backend response; carries the node the compiler blamed, if any (400 from
 * CompileError), so the caller can highlight the offending node instead of a bare toast. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly nodeId: string | null;

  constructor(status: number, envelope: ErrorEnvelope, nodeId: string | null) {
    super(envelope.message);
    this.status = status;
    this.code = envelope.code;
    this.nodeId = nodeId;
  }
}

// A compile error's node id isn't in the error envelope (server logs it, doesn't echo it back) —
// the message text embeds "(node <id>)" per CompileError.__str__; parse it back out here so the
// canvas can still highlight the right node without a backend contract change.
function extractNodeId(message: string): string | null {
  const match = /\(node ([^)]+)\)/.exec(message);
  return match ? match[1] : null;
}

const API_KEY_STORAGE_KEY = "lzt-flow.api-key";

// Restored eagerly at module load so a page refresh keeps the operator authenticated without a
// re-prompt; sessionStorage (not localStorage) so the key doesn't outlive the browser tab.
let apiKey: string | null = sessionStorage.getItem(API_KEY_STORAGE_KEY);

export function setApiKey(key: string): void {
  apiKey = key;
  sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function getApiKey(): string | null {
  return apiKey;
}

// Exported so tasksClient shares this exact error path. A second fetch helper would mean a second
// place that decides what an ErrorEnvelope is and a second way for an error to reach the UI.
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  const resp = await fetch(`/api${path}`, {
    ...init,
    headers: { ...headers, ...init?.headers },
  });
  if (!resp.ok) {
    // A 502/504 from a gateway (or any infra failure before the app handler runs) responds with
    // an HTML error page, not the JSON envelope — awaiting resp.json() there throws "Unexpected
    // token <" and hides the real HTTP status behind a parse error.
    const contentType = resp.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      throw new ApiError(
        resp.status,
        { code: "ERR-HTTP", message: `HTTP ${resp.status} — сервис недоступен`, request_id: "" },
        null,
      );
    }
    const envelope = (await resp.json()) as ErrorEnvelope;
    throw new ApiError(resp.status, envelope, extractNodeId(envelope.message));
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

// The catalog is a static server-side registry, but two independent consumers (FlowCanvas and
// useDynamicMethods) ask for it on every mount — that was four identical requests per screen.
// One in-flight promise, shared.
let catalogPromise: Promise<CatalogNode[]> | null = null;

export function fetchCatalog(): Promise<CatalogNode[]> {
  catalogPromise ??= request<CatalogListResponse>("/catalog/list")
    .then((body) => {
      if (body.schema_version !== CATALOG_SCHEMA_VERSION) {
        // A newer catalog may constrain what is safe to wire in ways this build cannot see.
        // Refusing beats rendering a form from half-understood metadata.
        throw new ApiError(
          200,
          {
            code: "ERR-HTTP",
            message: `Каталог версии ${body.schema_version} — обновите интерфейс`,
            request_id: "",
          },
          null,
        );
      }
      return body.nodes;
    })
    .catch((err: unknown) => {
      catalogPromise = null; // a failed fetch must not be cached as the answer
      throw err;
    });
  return catalogPromise;
}

export interface MarketCategoryDTO {
  slug: string;
  label: string;
}

/** GET /catalog/categories — market categories (live from pylzt) for the category_picker. */
export function fetchCategories(): Promise<MarketCategoryDTO[]> {
  return request<MarketCategoryDTO[]>("/catalog/categories");
}

export function createFlow(spec: FlowSpec): Promise<FlowCreatedResponse> {
  return request<FlowCreatedResponse>("/flows/create", { method: "POST", body: JSON.stringify(spec) });
}

/** Republish an already-saved flow in place. Routing an edit through createFlow would fork it. */
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

/** Labelled outgoing control-flow ports per node type (app/domain/flow_engine/ir_node.py docstring:
 * "next" for linear flow, "true"/"false" for condition, "body"/"after" for the fan-out nodes).
 * Not part of the catalog response — the catalog only carries *input* schema. */
const OUTPUT_PORTS: Record<string, string[]> = {
  condition: ["true", "false"],
  for_each_lot: ["body", "after"],
  for_each_account: ["body", "after"],
};

export function outputPortsFor(catalogKey: string): string[] {
  return OUTPUT_PORTS[catalogKey] ?? ["next"];
}

// AutoForm keeps number fields as the raw typed string (so a trailing "." doesn't get eaten
// mid-edit) — coerce a complete numeric string to a JS number right before it leaves the client.
function coerceNumericString(value: string | number | boolean): string | number | boolean {
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

const BATCH_CATALOG_KEY = "logic.batch";

// Nested children carry no canvas position/edges of their own (they're not draggable nodes on the
// graph) — only type+inputs, recursively, matching backend NodeSpec.children's own recursive shape.
function buildChildNodeSpec(child: CanvasChildNodeSpec): NodeSpec {
  const inputs: Record<string, InputSpec> = {};
  for (const [key, value] of Object.entries(child.values)) {
    if (value === "" || value === undefined) continue;
    inputs[key] = { literal: coerceNumericString(value) };
  }
  return {
    id: child.id,
    type: child.catalogKey,
    inputs,
    account_ref: null,
    edges: {},
    on_error: null,
    children:
      child.catalogKey === BATCH_CATALOG_KEY && child.children?.length
        ? child.children.map(buildChildNodeSpec)
        : undefined,
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
  const triggerNode = nodes.find((n) => n.data.category === "trigger");
  if (!triggerNode) {
    throw new FlowBuildError("добавьте на холст триггер-блок — без него флоу некому запускать");
  }

  const domainNodes = nodes.filter((n) => n.data.category !== "trigger");
  if (domainNodes.length === 0) {
    throw new FlowBuildError("добавьте хотя бы один action/logic блок");
  }

  const entryEdge = edges.find((e) => e.source === triggerNode.id);
  if (!entryEdge) {
    throw new FlowBuildError(
      "соедините триггер с первым блоком флоу",
      triggerNode.id,
    );
  }

  const nodeSpecs: NodeSpec[] = domainNodes.map((node) => {
    const outEdges: Record<string, string> = {};
    for (const edge of edges) {
      if (edge.source !== node.id) continue;
      const port = edge.sourceHandle ?? "next";
      outEdges[port] = edge.target;
    }
    const inputs: Record<string, InputSpec> = {};
    for (const [key, value] of Object.entries(node.data.values)) {
      if (value === "" || value === undefined) continue;
      inputs[key] = { literal: coerceNumericString(value) };
    }
    return {
      id: node.id,
      type: node.data.catalogKey,
      inputs,
      account_ref: null,
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

export interface FlowSummary {
  id: string;
  name: string;
}

/** The API names the identifier `flow_id`; the UI works with `id` everywhere. Rename at the
 * boundary — a raw wire object leaking into components silently yields `undefined` ids. */
interface FlowSummaryWire {
  flow_id: string;
  name: string;
}

/** Whether this stand actually enforces a key.
 *
 * `require_api_key` is a NO-OP when the server has no key configured, so a prompt shown
 * regardless would imply a boundary that does not exist — and ANY string typed into it would
 * appear to work, because the validating read succeeds for everyone. The gate has to ask before
 * it can tell a real lock from a painted one.
 */
export function authRequired(): Promise<{ required: boolean }> {
  return request<{ required: boolean }>("/auth/required");
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

/** GET /runs/{id}/trace answers a bare list of entries, and names the payload fields
 * inputs/output/iteration_key. The view speaks args/result/branch_id over a {run_id, steps}
 * object — reconcile here, at the one boundary that knows both. */
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

/** POST /flows/import and GET /flows/{id}/export speak an envelope, not a bare spec. */
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

/** The three import gates each fail with their own error code; map the code back to the gate so
 * the report can say WHERE the flow was rejected, not just that it was. */
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

/** Subscribes to a run's SSE feed. A malformed frame is logged and skipped rather than thrown —
 * one bad frame from the server must not tear down the whole live-trace view. Returns an
 * unsubscribe function that closes the underlying EventSource.
 *
 * EventSource cannot set custom request headers, so the X-API-Key that gates every other run read
 * cannot ride along here. Instead the key is spent once at POST /runs/{id}/stream-token — a normal
 * request, so it CAN carry the header — and the minute-long token it returns authorizes the stream
 * through the query string. That is why this function is async and why the connection opens on the
 * second round trip. */
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

interface StreamTokenResponse {
  token: string;
  expires_in: number;
}


/** One declared parameter on a composite template's surface. Per app/domain/flow_engine/model.py
 * TemplateParam: purely a naming contract. `output_port` is set only for outputs
 * ("<inner_node_id>.<port>", identifying which internal node/port produced the result) and is
 * null for inputs — an input's wiring lives in the template's own NodeSpec.inputs literals via a
 * `{{param.NAME}}` placeholder, not in this field. */
export interface TemplateParam {
  name: string;
  output_port: string | null;
}

export interface CreateCompositeRequest {
  name: string;
  nodes: NodeSpec[];
  entry_node_id: string;
  inputs: TemplateParam[];
  outputs: TemplateParam[];
}

export interface CompositeDetail {
  id: string;
  name: string;
  nodes: NodeSpec[];
  entry_node_id: string;
  inputs: TemplateParam[];
  outputs: TemplateParam[];
  created_at: string;
}

export interface CompositeSummary {
  id: string;
  name: string;
}

/** Wire shape of CompositeResponse — the API names the identifier `composite_id`. */
interface CompositeWire extends Omit<CompositeDetail, "id"> {
  composite_id: string;
}

function toCompositeDetail(wire: CompositeWire): CompositeDetail {
  const { composite_id, ...rest } = wire;
  return { id: composite_id, ...rest };
}

export function createComposite(body: CreateCompositeRequest): Promise<CompositeDetail> {
  return request<CompositeWire>("/composites/create", {
    method: "POST",
    body: JSON.stringify(body),
  }).then(toCompositeDetail);
}

// GET /composites/list returns the full CompositeResponse shape (mirrors GET .../:id) — trimmed
// down client-side since the list view only ever renders id+name.
export function listComposites(): Promise<CompositeSummary[]> {
  return request<CompositeWire[]>("/composites/list").then((list) =>
    list.map((composite) => ({ id: composite.composite_id, name: composite.name })),
  );
}

export function getComposite(compositeId: string): Promise<CompositeDetail> {
  return request<CompositeWire>(`/composites/${compositeId}`).then(toCompositeDetail);
}
