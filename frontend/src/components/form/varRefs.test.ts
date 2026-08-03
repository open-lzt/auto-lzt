import { describe, expect, it } from "vitest";
import { diagnoseVarRef, mentionedKeys, wholeRefKey } from "./varRefs";

describe("wholeRefKey — mirrors the backend anchor", () => {
  it("accepts the ref as the entire value, with or without inner spaces", () => {
    expect(wholeRefKey("{{vars.price}}")).toBe("price");
    expect(wholeRefKey("{{ vars.price }}")).toBe("price");
  });

  it("rejects a ref embedded in text — the backend would not substitute it either", () => {
    expect(wholeRefKey("цена {{vars.price}} руб")).toBeNull();
  });

  it("ignores non-strings", () => {
    expect(wholeRefKey(42)).toBeNull();
    expect(wholeRefKey(true)).toBeNull();
  });
});

describe("mentionedKeys", () => {
  it("finds every ref, embedded ones included", () => {
    expect(mentionedKeys("от {{vars.min}} до {{vars.max}}")).toEqual(["min", "max"]);
  });
});

describe("diagnoseVarRef", () => {
  it("says nothing about a literal", () => {
    expect(diagnoseVarRef("100", ["price"])).toBeNull();
  });

  it("says nothing about a correct whole-value ref", () => {
    expect(diagnoseVarRef("{{vars.price}}", ["price"])).toBeNull();
  });

  it("flags a ref embedded in text as partial", () => {
    expect(diagnoseVarRef("цена {{vars.price}} руб", ["price"])?.kind).toBe("partial");
  });

  it("flags an undeclared key before complaining about placement", () => {
    const problem = diagnoseVarRef("{{vars.ghost}}", ["price"]);
    expect(problem?.kind).toBe("unknown");
    expect(problem?.message).toContain("ghost");
  });
});
