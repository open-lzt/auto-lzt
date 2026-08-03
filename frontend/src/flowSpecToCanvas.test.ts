import { describe, expect, it } from "vitest";
import { flowSpecToCanvas } from "./App";
import { buildFlowSpec } from "./canvas/buildFlowSpec";
import type { CatalogNode } from "./api/catalogClient";
import type { FlowSpec } from "./api/flowClient";
import type { CanvasChildNodeSpec, CanvasNodeData } from "./canvas/canvasTypes";
import type { Edge, Node } from "@xyflow/react";

const catalog = [
  { key: "logic.batch", category: "logic" },
  { key: "market.bump", category: "action" },
] as CatalogNode[];

describe("flowSpecToCanvas", () => {
  it("reopens a saved batch node with its steps intact", () => {
    // The regression this pins is silent and destructive: the load path used to rebuild node.data
    // without `children`, so opening a saved flow and publishing it again wrote back an empty batch
    // — the operator's steps were gone with nothing on screen to say so.
    const spec: FlowSpec = {
      name: "f",
      entry_node_id: "b1",
      params: [],
      nodes: [
        {
          id: "b1",
          type: "logic.batch",
          inputs: {},
          account_ref: null,
          edges: {},
          on_error: null,
          children: [
            {
              id: "c1",
              type: "market.bump",
              inputs: { lot_id: { literal: 42 } },
              account_ref: null,
              edges: {},
              on_error: null,
            },
          ],
        },
      ],
    };

    const { nodes } = flowSpecToCanvas(spec, catalog);
    const batch = nodes.find((n) => n.id === "b1");

    expect(batch?.data.children).toEqual([
      { id: "c1", catalogKey: "market.bump", values: { lot_id: 42 }, children: undefined },
    ]);
  });

  it("round-trips a batch flow back to the same spec", () => {
    const children: CanvasChildNodeSpec[] = [
      { id: "c1", catalogKey: "market.bump", values: { lot_id: 42 } },
    ];
    const canvasNodes: Node<CanvasNodeData>[] = [
      {
        id: "trigger-1",
        type: "trigger",
        position: { x: 0, y: 0 },
        data: { catalogKey: "manual", category: "trigger", label: "Вручную", values: {} },
      },
      {
        id: "b1",
        type: "logic",
        position: { x: 0, y: 0 },
        data: { catalogKey: "logic.batch", category: "logic", label: "Пакет", values: {}, children },
      },
    ];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "b1" }];

    const { spec } = buildFlowSpec("f", canvasNodes, edges);
    const reopened = flowSpecToCanvas(spec, catalog);
    const republished = buildFlowSpec("f", reopened.nodes, reopened.edges).spec;

    expect(republished.nodes).toEqual(spec.nodes);
  });
});
