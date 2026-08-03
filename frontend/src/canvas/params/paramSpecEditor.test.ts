import { describe, expect, it } from "vitest";
import type { ParamSpec } from "../../api/paramSpec";
import {
  coerceDefault,
  emptyParam,
  formatOptions,
  hasOptions,
  hasRange,
  moveParam,
  parseOptions,
  removeParam,
  updateParam,
  validateKey,
} from "./paramSpecEditor";

function spec(key: string, over: Partial<ParamSpec> = {}): ParamSpec {
  return { key, label: key, control: "text", required: false, ...over };
}

describe("validateKey — mirrors the backend's ^\\w+$", () => {
  it("rejects an empty key", () => {
    expect(validateKey("", 0, [])).toBe("Ключ обязателен");
  });

  it("rejects a character that would break the {{vars.x}} ref", () => {
    expect(validateKey("my-key", 0, [])).toMatch(/подчёркивание/);
    expect(validateKey("моё", 0, [])).toMatch(/подчёркивание/);
  });

  it("rejects a duplicate but not the param's own key", () => {
    const params = [spec("a"), spec("b")];
    expect(validateKey("a", 1, params)).toBe("Такой ключ уже есть");
    expect(validateKey("a", 0, params)).toBeNull();
  });
});

describe("control extras", () => {
  it("offers a range only where one exists", () => {
    expect(hasRange("number")).toBe(true);
    expect(hasRange("slider")).toBe(true);
    expect(hasRange("delay")).toBe(true);
    expect(hasRange("text")).toBe(false);
    expect(hasRange("category_picker")).toBe(false);
  });

  it("offers options only to the pickers that consume them", () => {
    expect(hasOptions("select")).toBe(true);
    expect(hasOptions("radio")).toBe(true);
    expect(hasOptions("multiselect")).toBe(true);
    expect(hasOptions("text")).toBe(false);
    expect(hasOptions("toggle")).toBe(false);
  });
});

describe("updateParam", () => {
  it("drops the range when the control loses it", () => {
    const params = [spec("a", { control: "number", minimum: 1, maximum: 9, step: 1 })];
    const next = updateParam(params, 0, { control: "text" });
    expect(next[0].minimum).toBeUndefined();
    expect(next[0].maximum).toBeUndefined();
    expect(next[0].step).toBeUndefined();
  });

  it("drops options when the control loses them", () => {
    const params = [spec("a", { control: "select", options: [{ value: "x", label: "X" }] })];
    expect(updateParam(params, 0, { control: "text" })[0].options).toBeUndefined();
  });

  it("retypes the default to the new control", () => {
    const params = [spec("a", { control: "text", default: "10" })];
    expect(updateParam(params, 0, { control: "number" })[0].default).toBe(10);
    const flag = [spec("b", { control: "text", default: "true" })];
    expect(updateParam(flag, 0, { control: "toggle" })[0].default).toBe(true);
  });

  it("touches only the param at the index", () => {
    const params = [spec("a"), spec("b")];
    expect(updateParam(params, 1, { label: "Б" })[0]).toEqual(params[0]);
  });
});

describe("coerceDefault", () => {
  it("treats an emptied field as no default at all, not as null", () => {
    // `default: null` would reach the backend as a declared default; resolve_params only skips a
    // default that is absent.
    expect(coerceDefault("text", "")).toBeUndefined();
    expect(coerceDefault("number", null)).toBeUndefined();
  });

  it("keeps a non-numeric entry visible instead of silently zeroing it", () => {
    expect(coerceDefault("number", "abc")).toBeNull();
  });
});

describe("list operations", () => {
  it("moves within bounds and refuses to move past an end", () => {
    const params = [spec("a"), spec("b")];
    expect(moveParam(params, 0, 1).map((p) => p.key)).toEqual(["b", "a"]);
    expect(moveParam(params, 0, -1)).toBe(params);
    expect(moveParam(params, 1, 1)).toBe(params);
  });

  it("removes by index", () => {
    expect(removeParam([spec("a"), spec("b")], 0).map((p) => p.key)).toEqual(["b"]);
  });

  it("never proposes a key that already exists", () => {
    expect(emptyParam([spec("param_1")]).key).not.toBe("param_1");
  });
});

describe("options round trip", () => {
  it("survives format → parse unchanged", () => {
    const options = [
      { value: "steam", label: "Steam" },
      { value: "other", label: "other" },
    ];
    expect(parseOptions(formatOptions(options))).toEqual(options);
  });

  it("uses the value as the label when no label is given", () => {
    expect(parseOptions("steam")).toEqual([{ value: "steam", label: "steam" }]);
  });

  it("ignores blank lines", () => {
    expect(parseOptions("a\n\n  \nb")).toHaveLength(2);
  });
});
