import { request, type JsonSchema } from "../../../api/flowClient";

export interface PresetSummary {
  key: string;
  title: string;
  icon: string;
  default_name: string;
  /** The preset's parameter model as JSON Schema — what AutoForm renders. The server is the only
   * place a preset's fields are declared; the panel never restates them. */
  params_schema: JsonSchema;
}

export interface PresetDeployed {
  flow_id: string;
  trigger_id: string;
}

export function fetchPresets(): Promise<PresetSummary[]> {
  return request<PresetSummary[]>("/panel/presets/list");
}

/** Deploys the preset as an ordinary flow with a schedule attached. The result opens on the
 * canvas — a preset is a way to author a graph, not a second runtime. */
export function deployPreset(
  key: string,
  params: Record<string, unknown>,
): Promise<PresetDeployed> {
  return request<PresetDeployed>(`/panel/presets/${key}/deploy`, {
    method: "POST",
    body: JSON.stringify({ params }),
  });
}

/** Parses a free-text list of thread ids.
 *
 * Accepts commas, spaces and newlines, and tolerates a pasted thread URL — people copy the
 * address bar far more often than they copy a bare number, and the id is the LAST number in
 * `…/threads/slug.123456/`. Deduped, order-preserving.
 */
export function parseIdList(raw: string): number[] {
  const seen = new Set<number>();
  for (const chunk of raw.split(/[\s,]+/)) {
    if (!chunk) continue;
    const numbers = chunk.match(/\d+/g);
    if (!numbers) continue;
    const id = Number(numbers[numbers.length - 1]);
    if (Number.isSafeInteger(id) && id > 0) seen.add(id);
  }
  return [...seen];
}

/** Turn AutoForm's flat string values into the JSON the preset model expects.
 *
 * AutoForm keeps every value as a string while editing (a live Number() eats a trailing "." and
 * breaks decimal entry). The schema is what says which strings are really numbers, arrays or
 * booleans, so the coercion is driven by it rather than by guessing per key.
 */
export function coerceParams(
  schema: JsonSchema,
  values: Record<string, string | number | boolean>,
): Record<string, unknown> {
  const properties = schema.properties ?? {};
  const out: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(values)) {
    const prop = properties[key];
    if (prop === undefined || raw === "") continue;
    const type = prop.type;
    if (type === "array") {
      const items = (prop.items as JsonSchema | undefined)?.type;
      if (items === "integer" || items === "number") {
        out[key] = parseIdList(String(raw));
      } else {
        // Already a JSON array string from MultiSelect / AccountMultiPicker.
        try {
          const parsed: unknown = JSON.parse(String(raw));
          out[key] = Array.isArray(parsed) ? parsed : [];
        } catch {
          out[key] = [];
        }
      }
      continue;
    }
    if (type === "integer") out[key] = Number.parseInt(String(raw), 10);
    else if (type === "number") out[key] = Number(raw);
    else if (type === "boolean") out[key] = Boolean(raw);
    else out[key] = raw;
  }
  return out;
}

/** The values a form starts with, taken from the schema's own defaults so the server decides
 * what "unset" means — including that a money-spending preset starts as a dry run. */
export function defaultValues(schema: JsonSchema): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    const fallback = prop.default;
    if (typeof fallback === "string" || typeof fallback === "number" || typeof fallback === "boolean") {
      out[key] = fallback;
    } else if (Array.isArray(fallback)) {
      out[key] = JSON.stringify(fallback);
    }
  }
  return out;
}
