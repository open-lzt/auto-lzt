import { useMemo } from "react";
import type { Edge, Node } from "@xyflow/react";

interface ConnectedChain {
  highlightedNodeIds: Set<string>;
  highlightedEdgeIds: Set<string>;
}

function walk(startId: string, edges: Edge[], direction: "upstream" | "downstream"): { nodeIds: Set<string>; edgeIds: Set<string> } {
  const nodeIds = new Set<string>([startId]);
  const edgeIds = new Set<string>();
  let frontier = [startId];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const edge of edges) {
        const [from, to] = direction === "upstream" ? [edge.target, edge.source] : [edge.source, edge.target];
        if (from === id && !nodeIds.has(to)) {
          nodeIds.add(to);
          edgeIds.add(edge.id);
          next.push(to);
        }
      }
    }
    frontier = next;
  }
  return { nodeIds, edgeIds };
}

/** Highlights the full chain reachable from `hoveredId` in either direction (upstream sources +
 * downstream targets), for the hover-highlight effect on the canvas. Cycle-safe via visited sets. */
export function useConnectedChain(nodes: Node[], edges: Edge[], hoveredId: string | null): ConnectedChain {
  return useMemo(() => {
    if (!hoveredId) {
      return { highlightedNodeIds: new Set<string>(), highlightedEdgeIds: new Set<string>() };
    }
    const upstream = walk(hoveredId, edges, "upstream");
    const downstream = walk(hoveredId, edges, "downstream");
    return {
      highlightedNodeIds: new Set<string>([...upstream.nodeIds, ...downstream.nodeIds]),
      highlightedEdgeIds: new Set<string>([...upstream.edgeIds, ...downstream.edgeIds]),
    };
  }, [nodes, edges, hoveredId]);
}
