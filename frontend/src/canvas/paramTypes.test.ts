import { describe, expect, it } from "vitest";

import { MARKET_CATEGORIES, validateParam } from "./paramTypes";
import type { ParamSpec } from "./paramTypes";

function spec(partial: Partial<ParamSpec>): ParamSpec {
  return { key: "k", label: "K", control: "number", required: true, ...partial };
}

describe("validateParam", () => {
  it("flags a required empty value", () => {
    expect(validateParam(spec({ required: true }), null)).toBe("Required");
  });

  it("allows an optional empty value", () => {
    expect(validateParam(spec({ required: false }), null)).toBeNull();
  });

  it("allows a required-but-defaulted empty value", () => {
    expect(validateParam(spec({ required: true, default: 5 }), null)).toBeNull();
  });

  it("rejects a non-numeric number", () => {
    expect(validateParam(spec({ control: "number" }), "abc")).toBe("Expected a number");
  });

  it("enforces minimum and maximum", () => {
    expect(validateParam(spec({ control: "slider", minimum: 1 }), 0)).toBe("Must be ≥ 1");
    expect(validateParam(spec({ control: "slider", maximum: 10 }), 11)).toBe("Must be ≤ 10");
  });

  it("passes a valid number", () => {
    expect(validateParam(spec({ control: "number", minimum: 1, maximum: 10 }), 5)).toBeNull();
  });

  it("requires a boolean for a toggle", () => {
    expect(validateParam(spec({ control: "toggle" }), "yes")).toBe("Expected a switch value");
    expect(validateParam(spec({ control: "toggle" }), true)).toBeNull();
  });
});

describe("MARKET_CATEGORIES", () => {
  it("mirrors the 26 backend categories with unique slugs", () => {
    expect(MARKET_CATEGORIES).toHaveLength(26);
    const slugs = new Set(MARKET_CATEGORIES.map((c) => c.value));
    expect(slugs.size).toBe(26);
    expect(slugs.has("steam")).toBe(true);
  });
});
