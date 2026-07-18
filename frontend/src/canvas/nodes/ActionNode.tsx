import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { CanvasNodeData } from "../canvasTypes";
import { nodeDescription } from "../labels";
import { NodeShell } from "./shared";

export function ActionNode({ data, selected }: NodeProps<Node<CanvasNodeData>>) {
  return (
    <NodeShell
      category="action"
      label={data.label}
      description={nodeDescription(data.catalogKey)}
      selected={selected}
      errorMessage={data.errorMessage}
    >
      <Handle type="target" position={Position.Top} />
      <Handle type="source" id="next" position={Position.Bottom} />
    </NodeShell>
  );
}
