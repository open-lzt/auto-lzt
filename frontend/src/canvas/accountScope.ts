// account_ref isn't in the catalog's input schema, so the list below mirrors node sources by hand.
import type { Edge, Node } from "@xyflow/react";
import type { CanvasNodeData } from "./canvasTypes";

export const ACCOUNT_LOOP_KEY = "logic.for_each_account";

// Nodes that raise rather than fall back to the token pool when no account is available.
export const ACCOUNT_REQUIRED_KEYS: ReadonlySet<string> = new Set([
  "logic.get_my_lots", // get_my_lots.py:44 → NoAvailableAccount
  "market.relist", // relist.py:124     → NoAvailableAccount
  "forum.bump_thread", // bump_thread.py:56 → NoAvailableAccount
]);

export function needsAccount(catalogKey: string): boolean {
  return ACCOUNT_REQUIRED_KEYS.has(catalogKey);
}

// Visited set guards against cycles: the compiler rejects them server-side, but the canvas graph
// may still contain one while the operator is drawing, which would hang a naive traversal.
export function isInsideAccountLoop(
  nodeId: string,
  nodes: Node<CanvasNodeData>[],
  edges: Edge[],
): boolean {
  const targetsBySource = new Map<string, string[]>();
  for (const edge of edges) {
    const targets = targetsBySource.get(edge.source);
    if (targets) targets.push(edge.target);
    else targetsBySource.set(edge.source, [edge.target]);
  }

  const queue = nodes
    .filter((node) => node.data.catalogKey === ACCOUNT_LOOP_KEY)
    .map((node) => node.id);
  const visited = new Set<string>(queue);

  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const next of targetsBySource.get(current) ?? []) {
      if (next === nodeId) return true;
      if (visited.has(next)) continue;
      visited.add(next);
      queue.push(next);
    }
  }
  return false;
}
