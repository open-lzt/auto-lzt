// Lives here (not canvas/) to avoid a bidirectional import — canvas/ already imports components/form.

// Mirror of app/domain/flow_engine/compiler.py:35 — anchored both ends: backend substitutes only whole-value refs.
const WHOLE_VAR_REF = /^\{\{\s*vars\.(\w+)\s*\}\}$/;

const ANY_VAR_REF = /\{\{\s*vars\.(\w+)\s*\}\}/g;

/** The param key this value resolves to, or null if the value is a literal. */
export function wholeRefKey(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return WHOLE_VAR_REF.exec(value)?.[1] ?? null;
}

/** Every param key mentioned anywhere in the value, including refs the backend will not honour. */
export function mentionedKeys(value: unknown): string[] {
  if (typeof value !== "string") return [];
  return [...value.matchAll(ANY_VAR_REF)].map((m) => m[1]);
}

export interface VarRefProblem {
  kind: "partial" | "unknown";
  message: string;
}

// `partial`: "цена {{vars.p}} руб" errors nowhere — backend stores it as a literal, braces and all.
export function diagnoseVarRef(value: unknown, declaredKeys: readonly string[]): VarRefProblem | null {
  const mentioned = mentionedKeys(value);
  if (mentioned.length === 0) return null;

  const unknown = mentioned.find((k) => !declaredKeys.includes(k));
  if (unknown) {
    return { kind: "unknown", message: `параметр «${unknown}» не объявлен — подстановки не будет` };
  }
  if (wholeRefKey(value) === null) {
    return {
      kind: "partial",
      message:
        "подставляется только значение целиком: уберите остальной текст из поля, иначе строка уедет как есть, вместе со скобками",
    };
  }
  return null;
}
