// Undo/redo for the canvas graph — @xyflow/react 12 ships no history of its own.
import type { Edge, Node, NodeChange, EdgeChange } from "@xyflow/react";
import { useCallback, useReducer, useRef } from "react";
import type { CanvasNodeData } from "../canvasTypes";

export interface GraphSnapshot {
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
}

// Whole-graph snapshots, not deltas — a realistic flow is tens of nodes, so this stays cheap.
export const HISTORY_DEPTH = 50;

export interface GraphHistory {
  // Snapshots the CURRENT graph. Call it immediately BEFORE a structural mutation, never after.
  capture: () => void;
  undo: () => void;
  redo: () => void;
  reset: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

// `select`/`dimensions` excluded — clicking a node would otherwise fill the undo stack with
// look-alike states. A drag is recorded once on release; `dragging: true` fires per frame.
export function isStructuralNodeChange(changes: NodeChange<Node<CanvasNodeData>>[]): boolean {
  return changes.some(
    (c) =>
      c.type === "add" ||
      c.type === "remove" ||
      c.type === "replace" ||
      (c.type === "position" && c.dragging === false),
  );
}

export function isStructuralEdgeChange(changes: EdgeChange[]): boolean {
  return changes.some((c) => c.type === "add" || c.type === "remove" || c.type === "replace");
}

interface UseGraphHistoryArgs {
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
  setNodes: (nodes: Node<CanvasNodeData>[]) => void;
  setEdges: (edges: Edge[]) => void;
}

// One instance owns one graph — a shared stack would make Ctrl+Z inside a composite editor undo a
// step on the flow canvas behind it.
export function useGraphHistory({ nodes, edges, setNodes, setEdges }: UseGraphHistoryArgs): GraphHistory {
  // A ref, not the closure: `capture` runs from event handlers built on an older render, and a
  // stale closure would snapshot a graph two edits behind.
  const latest = useRef<GraphSnapshot>({ nodes, edges });
  latest.current = { nodes, edges };

  const past = useRef<GraphSnapshot[]>([]);
  const future = useRef<GraphSnapshot[]>([]);
  // Stacks are refs (must not trigger a render mid-mutation); this forces the re-render canUndo/
  // canRedo need.
  const [, bump] = useReducer((n: number) => n + 1, 0);

  const capture = useCallback(() => {
    past.current = [...past.current, latest.current].slice(-HISTORY_DEPTH);
    future.current = []; // a new edit after an undo makes the redo branch unreachable
    bump();
  }, []);

  const apply = useCallback(
    (snapshot: GraphSnapshot) => {
      setNodes(snapshot.nodes);
      setEdges(snapshot.edges);
    },
    [setNodes, setEdges],
  );

  const undo = useCallback(() => {
    const previous = past.current.at(-1);
    if (!previous) return;
    past.current = past.current.slice(0, -1);
    future.current = [...future.current, latest.current];
    apply(previous);
    bump();
  }, [apply]);

  const redo = useCallback(() => {
    const next = future.current.at(-1);
    if (!next) return;
    future.current = future.current.slice(0, -1);
    past.current = [...past.current, latest.current];
    apply(next);
    bump();
  }, [apply]);

  const reset = useCallback(() => {
    past.current = [];
    future.current = [];
    bump();
  }, []);

  return {
    capture,
    undo,
    redo,
    reset,
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
  };
}
