import type { Edge, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { buildFlowSpec } from "./flowClient";
import type { CanvasNodeData } from "../canvas/canvasTypes";

function triggerNode(): Node<CanvasNodeData> {
  return {
    id: "trigger-1",
    type: "trigger",
    position: { x: 0, y: 0 },
    data: { catalogKey: "manual", category: "trigger", label: "Вручную", values: {} },
  };
}

describe("buildFlowSpec — logic.batch children serialization", () => {
  it("emits no children field for a non-batch node", () => {
    const nodes: Node<CanvasNodeData>[] = [
      triggerNode(),
      {
        id: "n1",
        type: "action",
        position: { x: 0, y: 0 },
        data: { catalogKey: "market.bump", category: "action", label: "Поднять лот", values: {} },
      },
    ];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "n1" }];
    const { spec } = buildFlowSpec("f", nodes, edges);
    expect(spec.nodes[0].children).toBeUndefined();
  });

  it("serializes a logic.batch node's nested children recursively into NodeSpec.children", () => {
    const nodes: Node<CanvasNodeData>[] = [
      triggerNode(),
      {
        id: "batch-1",
        type: "logic",
        position: { x: 0, y: 0 },
        data: {
          catalogKey: "logic.batch",
          category: "logic",
          label: "Пакет шагов",
          values: {},
          children: [
            { id: "c1", catalogKey: "market.bump", values: { lot_id: "42" } },
            {
              id: "c2",
              catalogKey: "logic.batch",
              values: {},
              children: [{ id: "c3", catalogKey: "market.reprice", values: {} }],
            },
          ],
        },
      },
    ];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "batch-1" }];
    const { spec } = buildFlowSpec("f", nodes, edges);

    const batchSpec = spec.nodes[0];
    expect(batchSpec.type).toBe("logic.batch");
    expect(batchSpec.children).toHaveLength(2);
    expect(batchSpec.children?.[0]).toMatchObject({
      id: "c1",
      type: "market.bump",
      inputs: { lot_id: { literal: 42 } },
      edges: {},
    });
    // Nested batch-in-batch recurses through the same builder.
    expect(batchSpec.children?.[1].children).toEqual([
      expect.objectContaining({ id: "c3", type: "market.reprice" }),
    ]);
  });

  it("omits children for a logic.batch node with no nested children", () => {
    const nodes: Node<CanvasNodeData>[] = [
      triggerNode(),
      {
        id: "batch-1",
        type: "logic",
        position: { x: 0, y: 0 },
        data: { catalogKey: "logic.batch", category: "logic", label: "Пакет шагов", values: {} },
      },
    ];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "batch-1" }];
    const { spec } = buildFlowSpec("f", nodes, edges);
    expect(spec.nodes[0].children).toBeUndefined();
  });
});
