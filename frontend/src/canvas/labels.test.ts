import { describe, expect, it } from "vitest";
import { displayLabel, nodeDescription } from "./labels";

describe("displayLabel", () => {
  it("returns the RU label for a known node type", () => {
    expect(displayLabel("market.bump")).toBe("Поднять лот");
  });

  it("prefers facadeMethod over nodeType when both are known", () => {
    expect(displayLabel("action", "market.reprice")).toBe("Изменить цену");
  });

  it("returns the RU label for wave-02 logic node types", () => {
    expect(displayLabel("bool_op")).toBe("Логическая операция");
    expect(displayLabel("compare")).toBe("Сравнение");
    expect(displayLabel("switch")).toBe("Переключатель");
  });

  it("returns the RU label for wave-06 concurrency node types", () => {
    expect(displayLabel("fork")).toBe("Разветвление");
    expect(displayLabel("join")).toBe("Слияние");
    expect(displayLabel("batch_submit")).toBe("Отправить пакет");
  });

  it("returns the RU label for the actual registered wave-06 catalog keys (dotted, logic-prefixed)", () => {
    expect(displayLabel("logic.fork")).toBe("Разветвление");
    expect(displayLabel("logic.join")).toBe("Слияние");
    expect(displayLabel("logic.batch")).toBe("Пакет шагов");
    expect(displayLabel("logic.batch_status")).toBe("Статус пакета");
    expect(displayLabel("logic.batch_list_pending")).toBe("Список ожидающих");
  });

  it("humanizes an unmapped node type instead of returning it raw", () => {
    const result = displayLabel("custom.unknown_thing");
    expect(result).toBe("Custom Unknown Thing");
    expect(result).not.toContain(".");
    expect(result).not.toContain("_");
  });

  it("humanizes an unmapped facadeMethod", () => {
    expect(displayLabel("action", "market.new_feature")).toBe("Market New Feature");
  });
});

describe("nodeDescription", () => {
  it("returns a non-empty description for a known node type", () => {
    expect(nodeDescription("fork")).toBeTruthy();
  });

  it("returns a generic non-empty fallback for an unmapped node type", () => {
    const result = nodeDescription("totally.unknown");
    expect(result.length).toBeGreaterThan(0);
  });

  it("returns a real RU description (not the generic fallback) for the wave-06 catalog keys", () => {
    for (const key of ["logic.fork", "logic.join", "logic.batch", "logic.batch_status", "logic.batch_list_pending"]) {
      expect(nodeDescription(key)).not.toBe("Узел флоу без подробного описания");
    }
  });
});
