// Pure edit operations on a ParamSpec[]. Kept out of the component so the rules that must match
// the backend (key shape, uniqueness, which extras a control actually has) are testable without
// rendering anything.
import type { ParamControl, ParamOption, ParamSpec, ParamValue } from "../../api/paramSpec";

/** Mirror of the backend's key validator (app/domain/flow_engine/spec.py) — the key becomes
 * `{{vars.<key>}}`, so anything outside \w breaks the ref rather than the save. */
export const PARAM_KEY_PATTERN = /^\w+$/;

export const CONTROL_LABELS: Record<ParamControl, string> = {
  text: "Строка",
  textarea: "Многострочный текст",
  number: "Число",
  slider: "Ползунок",
  delay: "Задержка (секунды)",
  toggle: "Переключатель",
  select: "Список",
  radio: "Выбор одного",
  multiselect: "Выбор нескольких",
  account_picker: "Аккаунт",
  category_picker: "Категория",
  datetime: "Дата и время",
};

export const CONTROLS = Object.keys(CONTROL_LABELS) as ParamControl[];

/** Controls with a numeric range. Everything else must not show min/max/step — an input nobody
 * can fill honestly is worse than a missing one. */
const RANGED: ReadonlySet<ParamControl> = new Set(["number", "slider", "delay"]);
const OPTIONED: ReadonlySet<ParamControl> = new Set(["select", "radio", "multiselect"]);

export function hasRange(control: ParamControl): boolean {
  return RANGED.has(control);
}

export function hasOptions(control: ParamControl): boolean {
  return OPTIONED.has(control);
}

export function validateKey(key: string, index: number, params: ParamSpec[]): string | null {
  if (!key) return "Ключ обязателен";
  if (!PARAM_KEY_PATTERN.test(key)) return "Только латиница, цифры и подчёркивание";
  if (params.some((p, i) => i !== index && p.key === key)) return "Такой ключ уже есть";
  return null;
}

/** A default typed for the control it belongs to. Switching a control leaves the old default
 * behind otherwise — a string "10" under a toggle, which the backend then rejects at run time. */
export function coerceDefault(control: ParamControl, raw: ParamValue): ParamValue | undefined {
  // undefined, not null: `default: null` would travel to the backend as a declared default of
  // "nothing", and resolve_params treats a non-null default as the value to use.
  if (raw == null || raw === "") return undefined;
  if (control === "toggle") return raw === true || raw === "true";
  if (RANGED.has(control)) {
    const n = Number(raw);
    return Number.isNaN(n) ? null : n;
  }
  return String(raw);
}

export function emptyParam(params: ParamSpec[]): ParamSpec {
  let n = params.length + 1;
  while (params.some((p) => p.key === `param_${n}`)) n += 1;
  return { key: `param_${n}`, label: `Параметр ${n}`, control: "text", required: false };
}

export function updateParam(params: ParamSpec[], index: number, patch: Partial<ParamSpec>): ParamSpec[] {
  return params.map((p, i) => {
    if (i !== index) return p;
    const next = { ...p, ...patch };
    // Dropping the extras a control does not have keeps the saved spec honest: a leftover
    // `options` under a slider would survive every round trip and confuse the next reader.
    if (!hasRange(next.control)) {
      delete next.minimum;
      delete next.maximum;
      delete next.step;
    }
    if (!hasOptions(next.control)) delete next.options;
    if (patch.control && patch.control !== p.control && next.default != null) {
      next.default = coerceDefault(next.control, next.default);
    }
    return next;
  });
}

export function removeParam(params: ParamSpec[], index: number): ParamSpec[] {
  return params.filter((_, i) => i !== index);
}

export function moveParam(params: ParamSpec[], index: number, delta: -1 | 1): ParamSpec[] {
  const target = index + delta;
  if (target < 0 || target >= params.length) return params;
  const next = [...params];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** Options are edited as one line per `value|label` — a table of two columns for what is usually
 * three rows would be more chrome than content. */
export function parseOptions(text: string): ParamOption[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [value, ...rest] = line.split("|");
      const label = rest.join("|").trim();
      return { value: value.trim(), label: label || value.trim() };
    });
}

export function formatOptions(options: ParamOption[] | null | undefined): string {
  return (options ?? [])
    .map((o) => (o.label === String(o.value) ? String(o.value) : `${String(o.value)}|${o.label}`))
    .join("\n");
}
