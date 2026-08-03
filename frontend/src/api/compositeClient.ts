// Mirrors app/api/composite_routes.py and app/domain/flow_engine/model.py TemplateParam.
import { request } from "./httpClient";
import type { NodeSpec } from "./flowClient";

/** `output_port` is set only for outputs; inputs wire via NodeSpec.inputs `{{param.NAME}}` instead. */
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

export function listComposites(): Promise<CompositeSummary[]> {
  return request<CompositeWire[]>("/composites/list").then((list) =>
    list.map((composite) => ({ id: composite.composite_id, name: composite.name })),
  );
}

export function getComposite(compositeId: string): Promise<CompositeDetail> {
  return request<CompositeWire>(`/composites/${compositeId}`).then(toCompositeDetail);
}
