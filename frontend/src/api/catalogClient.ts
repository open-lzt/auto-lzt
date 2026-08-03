// Mirrors app/api/catalog_routes.py — keep in lockstep.
import { clientError, request } from "./httpClient";

export type NodeCategory = "action" | "logic" | "trigger";

/** Both halves are emitted by the backend at runtime — a switch narrowed to only one half is wrong. */
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
  options?: { value: string; label: string }[];
  /** Pydantic emits inherited fields first; without explicit order a shared base field jumps first. */
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

export type NodeCapability =
  | "market.read"
  | "market.mutate"
  | "network.egress"
  | "reflective"
  | "money"
  | "pure";

export interface CatalogNode {
  key: string;
  category: NodeCategory;
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  idempotent: boolean;
  capabilities: NodeCapability[];
}

export interface CatalogListResponse {
  schema_version: number;
  nodes: CatalogNode[];
}

/** Bump in lockstep with CATALOG_SCHEMA_VERSION in app/api/catalog_routes.py. */
export const CATALOG_SCHEMA_VERSION = 1;

let catalogPromise: Promise<CatalogNode[]> | null = null;

export function fetchCatalog(): Promise<CatalogNode[]> {
  catalogPromise ??= request<CatalogListResponse>("/catalog/list")
    .then((body) => {
      if (body.schema_version !== CATALOG_SCHEMA_VERSION) {
        throw clientError(`Каталог версии ${body.schema_version} — обновите интерфейс`);
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

export function fetchCategories(): Promise<MarketCategoryDTO[]> {
  return request<MarketCategoryDTO[]>("/catalog/categories");
}
