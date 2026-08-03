import type { Edge, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { ACCOUNT_LOOP_KEY, isInsideAccountLoop, needsAccount } from "./accountScope";
import type { CanvasNodeData } from "./canvasTypes";

function node(id: string, catalogKey: string): Node<CanvasNodeData> {
  return {
    id,
    type: "action",
    position: { x: 0, y: 0 },
    data: { catalogKey, category: "action", label: id, values: {} },
  };
}

function edge(source: string, target: string, sourceHandle?: string): Edge {
  return { id: `${source}->${target}`, source, target, sourceHandle };
}

describe("needsAccount", () => {
  it("names the three nodes that fail without an account", () => {
    expect(needsAccount("logic.get_my_lots")).toBe(true);
    expect(needsAccount("market.relist")).toBe(true);
    expect(needsAccount("forum.bump_thread")).toBe(true);
  });

  it("leaves the pool-backed nodes alone", () => {
    // Each of these has a *_via_pool path and runs with no account at all — flagging them would
    // demand a pin the operator does not owe.
    for (const key of ["market.bump", "market.reprice", "market.search", "market.fast_buy"]) {
      expect(needsAccount(key), key).toBe(false);
    }
  });
});

describe("isInsideAccountLoop", () => {
  const loop = node("loop", ACCOUNT_LOOP_KEY);

  it("finds a node one edge from the loop", () => {
    const nodes = [loop, node("a", "logic.get_my_lots")];
    expect(isInsideAccountLoop("a", nodes, [edge("loop", "a", "body")])).toBe(true);
  });

  it("finds a node three edges down the body chain", () => {
    const nodes = [loop, node("a", "market.bump"), node("b", "market.bump"), node("c", "market.relist")];
    const edges = [edge("loop", "a", "body"), edge("a", "b"), edge("b", "c")];
    expect(isInsideAccountLoop("c", nodes, edges)).toBe(true);
  });

  it("follows any port, not just body", () => {
    const nodes = [loop, node("after", "market.relist")];
    expect(isInsideAccountLoop("after", nodes, [edge("loop", "after", "after")])).toBe(true);
  });

  it("returns false for a node no loop reaches", () => {
    const nodes = [loop, node("a", "market.bump"), node("lonely", "logic.get_my_lots")];
    expect(isInsideAccountLoop("lonely", nodes, [edge("loop", "a", "body")])).toBe(false);
  });

  it("returns false when there is no loop at all", () => {
    const nodes = [node("a", "logic.get_my_lots")];
    expect(isInsideAccountLoop("a", nodes, [])).toBe(false);
  });

  it("terminates on a cyclic graph", () => {
    // The canvas allows a cycle — only the server-side compiler rejects one. A traversal without a
    // visited set hangs the editor here, which is why this test exists.
    const nodes = [loop, node("a", "market.bump"), node("b", "market.bump")];
    const edges = [edge("loop", "a", "body"), edge("a", "b"), edge("b", "a")];
    expect(isInsideAccountLoop("b", nodes, edges)).toBe(true);
    expect(isInsideAccountLoop("unrelated", nodes, edges)).toBe(false);
  });
});
