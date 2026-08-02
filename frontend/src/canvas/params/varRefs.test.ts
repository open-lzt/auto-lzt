import type { Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import type { CanvasNodeData } from "../canvasTypes";
import { nodesReferencing } from "./varRefs";

function node(label: string, values: Record<string, string | number | boolean>): Node<CanvasNodeData> {
  return {
    id: label,
    type: "action",
    position: { x: 0, y: 0 },
    data: { catalogKey: "market.bump", category: "action", label, values },
  };
}

describe("nodesReferencing", () => {
  it("names the nodes a deletion would break, embedded refs included", () => {
    const nodes = [
      node("Поднять лот", { amount: "{{vars.price}}" }),
      node("Пауза", { seconds: 5 }),
      node("Переставить", { note: "было {{vars.price}}" }),
    ];
    expect(nodesReferencing("price", nodes)).toEqual(["Поднять лот", "Переставить"]);
  });

  it("names nothing when no field mentions the key", () => {
    expect(nodesReferencing("other", [node("Пауза", { seconds: 5 })])).toEqual([]);
  });
});
