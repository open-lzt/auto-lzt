import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import type { CanvasNodeData } from "../canvasTypes";
import {
  HISTORY_DEPTH,
  isStructuralEdgeChange,
  isStructuralNodeChange,
  useGraphHistory,
} from "./useGraphHistory";

function node(id: string, values: Record<string, string> = {}): Node<CanvasNodeData> {
  return {
    id,
    type: "action",
    position: { x: 0, y: 0 },
    data: { catalogKey: "market.bump", category: "action", label: id, values },
  };
}

/** Drives the hook against real graph state, the way the canvas does — a hook tested against
 * hand-fed snapshots would pass while undo left the canvas untouched. */
function useHarness(initial: Node<CanvasNodeData>[] = []) {
  const [nodes, setNodes] = useState<Node<CanvasNodeData>[]>(initial);
  const [edges, setEdges] = useState<Edge[]>([]);
  const history = useGraphHistory({ nodes, edges, setNodes, setEdges });
  return { nodes, edges, setNodes, setEdges, history };
}

describe("useGraphHistory", () => {
  it("returns a deleted node together with its edges", () => {
    const { result } = renderHook(() => useHarness([node("a"), node("b")]));
    act(() => {
      result.current.setEdges([{ id: "a->b", source: "a", target: "b" }]);
    });

    act(() => {
      result.current.history.capture();
      result.current.setNodes([node("a")]);
      result.current.setEdges([]);
    });
    expect(result.current.nodes).toHaveLength(1);

    act(() => result.current.history.undo());
    expect(result.current.nodes.map((n) => n.id)).toEqual(["a", "b"]);
    expect(result.current.edges).toHaveLength(1);
  });

  it("redoes what it undid, and a fresh edit makes the redo branch unreachable", () => {
    const { result } = renderHook(() => useHarness([node("a")]));
    act(() => {
      result.current.history.capture();
      result.current.setNodes([node("a"), node("b")]);
    });

    act(() => result.current.history.undo());
    expect(result.current.history.canRedo).toBe(true);

    act(() => result.current.history.redo());
    expect(result.current.nodes.map((n) => n.id)).toEqual(["a", "b"]);

    act(() => result.current.history.undo());
    act(() => {
      result.current.history.capture();
      result.current.setNodes([node("a"), node("c")]);
    });
    expect(result.current.history.canRedo).toBe(false);
  });

  it("drops the oldest snapshot past the depth limit", () => {
    const { result } = renderHook(() => useHarness([node("n0")]));
    for (let i = 1; i <= HISTORY_DEPTH + 1; i += 1) {
      act(() => {
        result.current.history.capture();
        result.current.setNodes([node(`n${i}`)]);
      });
    }
    // Unwinding the whole stack cannot reach n0 any more: it fell off the bottom.
    for (let i = 0; i <= HISTORY_DEPTH + 1; i += 1) {
      act(() => result.current.history.undo());
    }
    expect(result.current.nodes[0].id).toBe("n1");
    expect(result.current.history.canUndo).toBe(false);
  });

  it("reset drops both stacks", () => {
    const { result } = renderHook(() => useHarness([node("a")]));
    act(() => {
      result.current.history.capture();
      result.current.setNodes([node("a"), node("b")]);
    });
    act(() => result.current.history.reset());
    expect(result.current.history.canUndo).toBe(false);
    expect(result.current.history.canRedo).toBe(false);
  });

  it("two instances keep separate stacks", () => {
    const first = renderHook(() => useHarness([node("a")]));
    const second = renderHook(() => useHarness([node("x")]));

    act(() => {
      first.result.current.history.capture();
      first.result.current.setNodes([node("a"), node("b")]);
    });

    expect(first.result.current.history.canUndo).toBe(true);
    expect(second.result.current.history.canUndo).toBe(false);
  });
});

describe("structural change filter", () => {
  it("ignores selection, hover geometry and an in-flight drag", () => {
    expect(isStructuralNodeChange([{ id: "a", type: "select", selected: true }])).toBe(false);
    expect(
      isStructuralNodeChange([{ id: "a", type: "dimensions", dimensions: { width: 10, height: 10 } }]),
    ).toBe(false);
    expect(
      isStructuralNodeChange([{ id: "a", type: "position", position: { x: 1, y: 1 }, dragging: true }]),
    ).toBe(false);
  });

  it("records a drag once, on release", () => {
    expect(
      isStructuralNodeChange([{ id: "a", type: "position", position: { x: 1, y: 1 }, dragging: false }]),
    ).toBe(true);
  });

  it("records add and remove for both nodes and edges", () => {
    expect(isStructuralNodeChange([{ id: "a", type: "remove" }])).toBe(true);
    expect(isStructuralEdgeChange([{ id: "e", type: "remove" }])).toBe(true);
    expect(isStructuralEdgeChange([{ id: "e", type: "select", selected: true }])).toBe(false);
  });
});
