import type { Edge, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { buildFlowSpec, childNodeSpecToCanvas } from "./buildFlowSpec";
import type { ParamSpec } from "../api/paramSpec";
import type { CanvasChildNodeSpec, CanvasNodeData } from "./canvasTypes";

function triggerNode(): Node<CanvasNodeData> {
  return {
    id: "trigger-1",
    type: "trigger",
    position: { x: 0, y: 0 },
    data: { catalogKey: "manual", category: "trigger", label: "Вручную", values: {} },
  };
}

function actionNode(id: string): Node<CanvasNodeData> {
  return {
    id,
    type: "action",
    position: { x: 0, y: 0 },
    data: { catalogKey: "market.bump", category: "action", label: "Поднять лот", values: {} },
  };
}

describe("buildFlowSpec — refusals", () => {
  it("refuses a second trigger instead of silently deploying the first", () => {
    // The graph is otherwise valid, so nothing downstream would have complained: the stray trigger
    // just vanished from the spec, taking its schedule with it.
    const second = { ...triggerNode(), id: "trigger-2" };
    const nodes = [triggerNode(), second, actionNode("n1")];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "n1" }];

    expect(() => buildFlowSpec("f", nodes, edges)).toThrow(/больше одного триггера/);
  });

  it("refuses a required param with no default on a scheduled flow, naming the param", () => {
    // The failure this replaces was invisible: the flow published fine and then failed on every
    // tick, since a schedule has no operator to fill the form.
    const scheduled: Node<CanvasNodeData> = {
      ...triggerNode(),
      data: {
        catalogKey: "schedule",
        category: "trigger",
        label: "По расписанию",
        values: {},
        triggerConfig: { kind: "schedule", schedule_cron: "0 * * * *", event_type: "" },
      },
    };
    const nodes = [scheduled, actionNode("n1")];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "n1" }];
    const params: ParamSpec[] = [
      { key: "limit", label: "Лимит", control: "number", required: true },
    ];

    expect(() => buildFlowSpec("f", nodes, edges, params)).toThrow(/«Лимит»/);
    // A default makes it fillable, and the same graph publishes.
    expect(() => buildFlowSpec("f", nodes, edges, [{ ...params[0], default: 10 }])).not.toThrow();
    // Manual trigger: there IS a form, so the same param is fine.
    expect(() => buildFlowSpec("f", [triggerNode(), actionNode("n1")], edges, params)).not.toThrow();
  });

  it("carries declared params into the spec unchanged", () => {
    const nodes = [triggerNode(), actionNode("n1")];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "n1" }];
    const params: ParamSpec[] = [
      { key: "price", label: "Цена", control: "number", required: false, default: 100, minimum: 1 },
    ];
    // The canvas does not own params, so the only thing that can lose them is this passthrough.
    const first = buildFlowSpec("f", nodes, edges, params).spec;
    expect(first.params).toEqual(params);
    expect(buildFlowSpec("f", nodes, edges, first.params ?? []).spec.params).toEqual(params);
  });

  it("refuses a canvas with no trigger", () => {
    expect(() => buildFlowSpec("f", [actionNode("n1")], [])).toThrow(/триггер-блок/);
  });

  it("refuses a canvas with no action or logic block", () => {
    expect(() => buildFlowSpec("f", [triggerNode()], [])).toThrow(/action\/logic/);
  });

  it("refuses a trigger wired to nothing", () => {
    const nodes = [triggerNode(), actionNode("n1")];
    expect(() => buildFlowSpec("f", nodes, [])).toThrow(/соедините триггер/);
  });
});

describe("buildFlowSpec — the pinned account", () => {
  function pinned(id: string, catalogKey: string, accountRef?: string): Node<CanvasNodeData> {
    return {
      id,
      type: "action",
      position: { x: 0, y: 0 },
      data: { catalogKey, category: "action", label: id, values: {}, accountRef },
    };
  }

  it("carries the pin into NodeSpec.account_ref", () => {
    const nodes = [triggerNode(), pinned("n1", "market.bump", "acc-1")];
    const { spec } = buildFlowSpec("f", nodes, [{ id: "e1", source: "trigger-1", target: "n1" }]);
    expect(spec.nodes[0].account_ref).toBe("acc-1");
  });

  it("sends null when nothing is pinned", () => {
    const nodes = [triggerNode(), pinned("n1", "market.bump")];
    const { spec } = buildFlowSpec("f", nodes, [{ id: "e1", source: "trigger-1", target: "n1" }]);
    expect(spec.nodes[0].account_ref).toBeNull();
  });

  it("refuses an account-bound node with no pin and no loop above it", () => {
    // Without this the flow publishes fine and dies minutes later inside the run with
    // NoAvailableAccount — the error arrives detached from the decision that caused it.
    const nodes = [triggerNode(), pinned("n1", "logic.get_my_lots")];
    expect(() =>
      buildFlowSpec("f", nodes, [{ id: "e1", source: "trigger-1", target: "n1" }]),
    ).toThrow(/нужен аккаунт/);
  });

  it("accepts the same node once it is pinned", () => {
    const nodes = [triggerNode(), pinned("n1", "logic.get_my_lots", "acc-1")];
    const { spec } = buildFlowSpec("f", nodes, [{ id: "e1", source: "trigger-1", target: "n1" }]);
    expect(spec.nodes[0].account_ref).toBe("acc-1");
  });

  it("accepts an unpinned account-bound node inside a for_each_account loop", () => {
    const loop: Node<CanvasNodeData> = {
      id: "loop",
      type: "logic",
      position: { x: 0, y: 0 },
      data: { catalogKey: "logic.for_each_account", category: "logic", label: "По аккаунтам", values: {} },
    };
    const nodes = [triggerNode(), loop, pinned("n1", "market.relist")];
    const edges: Edge[] = [
      { id: "e1", source: "trigger-1", target: "loop" },
      { id: "e2", source: "loop", target: "n1", sourceHandle: "body" },
    ];
    expect(() => buildFlowSpec("f", nodes, edges)).not.toThrow();
  });

  it("never demands a pin from a pool-backed node", () => {
    // These four fall back to *_via_pool server-side; demanding a pin would invent a requirement
    // the backend does not have — and would refuse flows that work today.
    for (const key of ["market.bump", "market.reprice", "market.search", "market.fast_buy"]) {
      const nodes = [triggerNode(), pinned("n1", key)];
      expect(() =>
        buildFlowSpec("f", nodes, [{ id: "e1", source: "trigger-1", target: "n1" }]),
      ).not.toThrow();
    }
  });
});

describe("childNodeSpecToCanvas — the inverse of buildChildNodeSpec", () => {
  it("survives a round trip so a republish cannot erase batch steps", () => {
    // The bug this pins: the load path rebuilt node.data without `children`, so reopening a saved
    // flow and publishing it again overwrote the stored batch steps with nothing.
    const children: CanvasChildNodeSpec[] = [
      { id: "c1", catalogKey: "market.bump", values: { lot_id: 42, note: "раз" } },
      {
        id: "c2",
        catalogKey: "logic.batch",
        values: {},
        children: [{ id: "c3", catalogKey: "market.reprice", values: { price: 100 } }],
      },
    ];
    const nodes: Node<CanvasNodeData>[] = [
      triggerNode(),
      {
        id: "b1",
        type: "logic",
        position: { x: 0, y: 0 },
        data: { catalogKey: "logic.batch", category: "logic", label: "Пакет", values: {}, children },
      },
    ];
    const edges: Edge[] = [{ id: "e1", source: "trigger-1", target: "b1" }];

    const { spec } = buildFlowSpec("f", nodes, edges);
    expect(spec.nodes[0].children?.map(childNodeSpecToCanvas)).toEqual(children);
  });
});

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
