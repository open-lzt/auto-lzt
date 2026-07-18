// Mirror of app/domain/flow_engine/spec.py ParamSpec (source of truth is the backend). Kept in
// lockstep by hand, the repo's existing DTO-mirroring convention.

export type ParamControl =
  | "text"
  | "number"
  | "slider"
  | "toggle"
  | "select"
  | "account_picker"
  | "category_picker"
  | "delay"
  | "multiselect"
  | "datetime"
  | "radio"
  | "textarea";

export interface ParamOption {
  value: string | number;
  label: string;
}

export type ParamValue = string | number | boolean | null;

export interface ParamVisibility {
  field: string;
  equals: string | number | boolean;
}

export interface ParamSpec {
  key: string;
  label: string;
  control: ParamControl;
  default?: ParamValue;
  required: boolean;
  description?: string | null;
  minimum?: number | null;
  maximum?: number | null;
  step?: number | null;
  options?: ParamOption[] | null;
  group?: string | null;
  visible_if?: ParamVisibility | null;
}

/** A param gated by ``visible_if`` is shown only when its controlling field matches. */
export function isVisible(spec: ParamSpec, values: Record<string, ParamValue>): boolean {
  if (!spec.visible_if) return true;
  return String(values[spec.visible_if.field] ?? "") === String(spec.visible_if.equals);
}

// WAVE-01 HARDCODE: mirror of app/domain/catalog/constants.py MARKET_CATEGORIES. Replaced in
// wave-03 when CategoryPicker fetches GET /catalog/categories.
export const MARKET_CATEGORIES: readonly { value: string; label: string }[] = [
  { value: "steam", label: "Steam" },
  { value: "fortnite", label: "Fortnite" },
  { value: "riot", label: "Riot (Valorant / LoL)" },
  { value: "telegram", label: "Telegram" },
  { value: "discord", label: "Discord" },
  { value: "roblox", label: "Roblox" },
  { value: "epicgames", label: "Epic Games" },
  { value: "battlenet", label: "Battle.net" },
  { value: "ea", label: "EA" },
  { value: "escapefromtarkov", label: "Escape from Tarkov" },
  { value: "gifts", label: "Gifts" },
  { value: "instagram", label: "Instagram" },
  { value: "minecraft", label: "Minecraft" },
  { value: "mihoyo", label: "miHoYo (Genshin / HSR)" },
  { value: "socialclub", label: "Social Club" },
  { value: "supercell", label: "Supercell" },
  { value: "tiktok", label: "TikTok" },
  { value: "uplay", label: "Uplay" },
  { value: "vpn", label: "VPN" },
  { value: "warface", label: "Warface" },
  { value: "wot", label: "World of Tanks" },
  { value: "wotblitz", label: "WoT Blitz" },
  { value: "hytale", label: "Hytale" },
  { value: "llm", label: "LLM / AI" },
  { value: "vkontakte", label: "VK" },
  { value: "other", label: "Other" },
];

/** Client-side validation mirroring resolve_params — returns an error string or null. Coercion to
 * the wire type happens in ParamSurface; this only gates the value before submit. */
export function validateParam(spec: ParamSpec, raw: ParamValue): string | null {
  const isEmpty = raw === null || raw === "";
  if (isEmpty) {
    return spec.required && spec.default == null ? "Required" : null;
  }
  if (spec.control === "toggle") {
    return typeof raw === "boolean" ? null : "Expected a switch value";
  }
  if (["number", "slider", "delay", "category_picker"].includes(spec.control)) {
    if (spec.control === "category_picker") return null; // slug string, validated by options
    const n = typeof raw === "number" ? raw : Number(raw);
    if (Number.isNaN(n)) return "Expected a number";
    if (spec.minimum != null && n < spec.minimum) return `Must be ≥ ${spec.minimum}`;
    if (spec.maximum != null && n > spec.maximum) return `Must be ≤ ${spec.maximum}`;
  }
  return null;
}
