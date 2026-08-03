import type { Node } from "@xyflow/react";
import { mentionedKeys } from "../../components/form/varRefs";
import type { CanvasNodeData } from "../canvasTypes";

/** Labels, not ids: the node id is invisible on the canvas. */
export function nodesReferencing(key: string, nodes: Node<CanvasNodeData>[]): string[] {
  const labels: string[] = [];
  for (const node of nodes) {
    const hit = Object.values(node.data.values).some((v) => mentionedKeys(v).includes(key));
    if (hit) labels.push(node.data.label);
  }
  return labels;
}
