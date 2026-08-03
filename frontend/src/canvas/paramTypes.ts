// The ParamSpec DTO itself lives in api/paramSpec.ts; this module only interprets it.
import type { ParamSpec, ParamValue } from "../api/paramSpec";

export type {
  ParamControl,
  ParamOption,
  ParamSpec,
  ParamValue,
  ParamVisibility,
} from "../api/paramSpec";

export function isVisible(spec: ParamSpec, values: Record<string, ParamValue>): boolean {
  if (!spec.visible_if) return true;
  return String(values[spec.visible_if.field] ?? "") === String(spec.visible_if.equals);
}

// Mirrors app/domain/catalog/constants.py MARKET_CATEGORIES — keep in sync.
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

/** Mirrors resolve_params server-side; wire-type coercion happens in ParamSurface, not here. */
export function validateParam(spec: ParamSpec, raw: ParamValue): string | null {
  const isEmpty = raw === null || raw === "";
  if (isEmpty) {
    return spec.required && spec.default == null ? "Обязательное поле" : null;
  }
  if (spec.control === "toggle") {
    return typeof raw === "boolean" ? null : "Ожидается переключатель";
  }
  if (["number", "slider", "delay", "category_picker"].includes(spec.control)) {
    if (spec.control === "category_picker") return null; // slug string, validated by options
    const n = typeof raw === "number" ? raw : Number(raw);
    if (Number.isNaN(n)) return "Ожидается число";
    if (spec.minimum != null && n < spec.minimum) return `Должно быть ≥ ${spec.minimum}`;
    if (spec.maximum != null && n > spec.maximum) return `Должно быть ≤ ${spec.maximum}`;
  }
  return null;
}
