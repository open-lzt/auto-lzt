import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import { useConnectedChain } from "./useConnectedChain";

function node(id: string): Node {
  return { id, position: { x: 0, y: 0 }, data: {} };
}

function edge(id: string, source: string, target: string): Edge {
  return { id, source, target };
}

describe("useConnectedChain", () => {
  it("returns empty sets when hoveredId is null", () => {
    const nodes = [node("a"), node("b")];
    const edges = [edge("e1", "a", "b")];
    const { result } = renderHook(() => useConnectedChain(nodes, edges, null));
    expect(result.current.highlightedNodeIds.size).toBe(0);
    expect(result.current.highlightedEdgeIds.size).toBe(0);
  });

  it("includes only the hovered node when it has no edges", () => {
    const nodes = [node("a"), node("b")];
    const edges: Edge[] = [];
    const { result } = renderHook(() => useConnectedChain(nodes, edges, "a"));
    expect(result.current.highlightedNodeIds).toEqual(new Set(["a"]));
    expect(result.current.highlightedEdgeIds.size).toBe(0);
  });

  it("walks both upstream and downstream from the hovered node", () => {
    // trigger -> a -> b -> c
    const nodes = [node("trigger"), node("a"), node("b"), node("c")];
    const edges = [edge("e1", "trigger", "a"), edge("e2", "a", "b"), edge("e3", "b", "c")];
    const { result } = renderHook(() => useConnectedChain(nodes, edges, "b"));
    expect(result.current.highlightedNodeIds).toEqual(new Set(["trigger", "a", "b", "c"]));
    expect(result.current.highlightedEdgeIds).toEqual(new Set(["e1", "e2", "e3"]));
  });

  it("does not include disconnected branches", () => {
    const nodes = [node("a"), node("b"), node("x"), node("y")];
    const edges = [edge("e1", "a", "b"), edge("e2", "x", "y")];
    const { result } = renderHook(() => useConnectedChain(nodes, edges, "a"));
    expect(result.current.highlightedNodeIds).toEqual(new Set(["a", "b"]));
    expect(result.current.highlightedEdgeIds).toEqual(new Set(["e1"]));
  });

  it("is cycle-safe (fork/join loop does not infinite-loop)", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const edges = [edge("e1", "a", "b"), edge("e2", "b", "c"), edge("e3", "c", "a")];
    const { result } = renderHook(() => useConnectedChain(nodes, edges, "a"));
    expect(result.current.highlightedNodeIds).toEqual(new Set(["a", "b", "c"]));
    expect(result.current.highlightedEdgeIds).toEqual(new Set(["e1", "e2", "e3"]));
  });
});
