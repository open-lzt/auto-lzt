import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { CanvasNodeData } from "../canvasTypes";
import { nodeDescription } from "../labels";
import { NodeShell } from "./shared";

const KIND_LABEL: Record<string, string> = {
  manual: "вручную",
  schedule: "по расписанию",
  event: "по событию",
};

export function TriggerNode({ data, selected }: NodeProps<Node<CanvasNodeData>>) {
  const config = data.triggerConfig;
  const detail =
    config?.kind === "schedule" && config.schedule_cron
      ? config.schedule_cron
      : config?.kind === "event" && config.event_type
        ? config.event_type
        : config
          ? KIND_LABEL[config.kind]
          : undefined;

  return (
    <NodeShell
      category="trigger"
      label={data.label}
      description={nodeDescription(data.catalogKey)}
      selected={selected}
      errorMessage={data.errorMessage}
    >
      {detail ? <span>{detail}</span> : null}
      <Handle type="source" id="next" position={Position.Bottom} />
    </NodeShell>
  );
}
