// The node catalog and its ui-hint vocabulary — what a node IS and how to draw a form for it.
// Mirrors app/api/catalog_routes.py.
import { clientError, request } from "./httpClient";

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
  /** What the node can do — the same vocabulary the module validator filters on. Rendered as a
   * badge so an operator can see that a node spends money before wiring it, not after. */
  capabilities: NodeCapability[];
}

/** GET /catalog/list's envelope. The version exists so this client refuses to render a shape it
 * does not understand rather than guessing at it. */
export interface CatalogListResponse {
  schema_version: number;
  nodes: CatalogNode[];
}

/** Bump in lockstep with CATALOG_SCHEMA_VERSION in app/api/catalog_routes.py. */
export const CATALOG_SCHEMA_VERSION = 1;

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

/** GET /catalog/categories — market categories (live from pylzt) for the category_picker. */
export function fetchCategories(): Promise<MarketCategoryDTO[]> {
  return request<MarketCategoryDTO[]>("/catalog/categories");
}
