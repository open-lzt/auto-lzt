import type { ParamControl, ParamOption, ParamSpec, ParamValue } from "../../api/paramSpec";

/** Mirrors backend key validator (app/domain/flow_engine/spec.py); outside \w breaks the {{vars.<key>}} ref. */
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

/** Retypes the default for the new control — else e.g. a string "10" survives under a toggle and the backend rejects it at run time. */
export function coerceDefault(control: ParamControl, raw: ParamValue): ParamValue | undefined {
  // undefined not null: resolve_params treats a non-null default as the value to use.
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
