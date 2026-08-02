// Composite templates — a saved subgraph with a declared input/output surface.
// Mirrors app/api/composite_routes.py and app/domain/flow_engine/model.py TemplateParam.
import { request } from "./httpClient";
import type { NodeSpec } from "./flowClient";

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
