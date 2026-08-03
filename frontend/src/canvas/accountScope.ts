// Which nodes need an account, and whether a given node already gets one from a loop.
//
// The backend has no flag for this: the catalog carries a node's INPUT schema, and the account is
// not an input — it is NodeSpec.account_ref, filled either statically or by for_each_account at
// run time. So the list below mirrors the node sources by hand, each line naming the file that
// proves it. See docs/decisions-later.md for the trigger that replaces this with a catalog flag.
import type { Edge, Node } from "@xyflow/react";
import type { CanvasNodeData } from "./canvasTypes";

export const ACCOUNT_LOOP_KEY = "logic.for_each_account";

/** Nodes that raise rather than fall back to the token pool when no account is available.
 *
 * Verified against the node sources, not inferred: each of these ends with a hard failure, while
 * market.bump / market.reprice / market.search / market.fast_buy all have a `*_via_pool` path and
 * work with no account at all. */
export const ACCOUNT_REQUIRED_KEYS: ReadonlySet<string> = new Set([
  "logic.get_my_lots", // get_my_lots.py:44 → NoAvailableAccount
  "market.relist", // relist.py:124     → NoAvailableAccount
  "forum.bump_thread", // bump_thread.py:56 → NoAvailableAccount
]);

export function needsAccount(catalogKey: string): boolean {
  return ACCOUNT_REQUIRED_KEYS.has(catalogKey);
}

/** True when the node is reachable from any for_each_account node, i.e. the runtime will hand it
 * an active_account_id and a static pin is unnecessary.
 *
 * The walk carries a visited set because the canvas graph may legitimately contain a cycle — the
 * compiler only rejects one server-side, so a naive traversal would hang the editor while the
 * operator is still drawing. */
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
