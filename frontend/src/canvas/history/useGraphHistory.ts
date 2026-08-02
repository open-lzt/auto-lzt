// Undo/redo for the canvas graph. @xyflow/react 12 ships no history of its own, and a third-party
// stack would be a wrapper with one backend — so the stack lives here.
import type { Edge, Node, NodeChange, EdgeChange } from "@xyflow/react";
import { useCallback, useReducer, useRef } from "react";
import type { CanvasNodeData } from "../canvasTypes";

export interface GraphSnapshot {
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
}

/** Whole-graph snapshots, not deltas: a realistic flow is tens of nodes, so 50 of them stay in the
 * low megabytes and deltas would optimise a problem nobody has measured. */
export const HISTORY_DEPTH = 50;

export interface GraphHistory {
  /** Snapshots the CURRENT graph. Call it immediately BEFORE a structural mutation, never after. */
  capture: () => void;
  undo: () => void;
  redo: () => void;
  /** Drops both stacks — for loading another flow, where the previous graph is not a step back. */
  reset: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

/** True for a change that alters the graph's STRUCTURE, as opposed to its presentation.
 *
 * `select` and `dimensions` are excluded deliberately: clicking a node would otherwise fill the
 * stack with states that look identical, and Ctrl+Z would spend its first ten presses undoing
 * selections. A drag is recorded once, on release — `dragging: true` fires per animation frame. */
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

/** One instance owns one graph. The composite editor (AuthoringMode) holds its own `useNodesState`,
 * so it gets its own instance — a shared stack would make Ctrl+Z inside a composite undo a step on
 * the flow canvas behind it. */
export function useGraphHistory({ nodes, edges, setNodes, setEdges }: UseGraphHistoryArgs): GraphHistory {
  // A ref, not the closure: `capture` is called from event handlers that were created on an older
  // render, and a stale closure would snapshot a graph two edits behind.
  const latest = useRef<GraphSnapshot>({ nodes, edges });
  latest.current = { nodes, edges };

  const past = useRef<GraphSnapshot[]>([]);
  const future = useRef<GraphSnapshot[]>([]);
  // The stacks are refs (they must not trigger a render when written mid-mutation), so this forces
  // the re-render that keeps canUndo/canRedo — and the buttons reading them — honest.
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
