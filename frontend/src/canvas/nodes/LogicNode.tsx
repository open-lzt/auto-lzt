import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { ReactNode } from "react";
import { outputPortsFor } from "../../api/flowClient";
import type { CanvasNodeData } from "../canvasTypes";
import { nodeDescription } from "../labels";
import { NodeShell } from "./shared";
import "./logic-node.css";

// Fork has no known branch count on the canvas today (edges/values don't carry it) — two static
// labeled handles is the documented simplification (see task brief) rather than a dynamic-handle UI.
const FORK_BRANCH_HANDLES = ["branch-1", "branch-2"];
const JOIN_BRANCH_HANDLES = ["branch-1", "branch-2"];

function ForkBody() {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      {FORK_BRANCH_HANDLES.map((id, i) => (
        <Handle
          key={id}
          type="source"
          id={id}
          position={Position.Bottom}
          style={{ left: `${((i + 1) / (FORK_BRANCH_HANDLES.length + 1)) * 100}%` }}
        />
      ))}
      <div className="logic-node__badge logic-node__badge--fork">∥ параллельные ветки</div>
    </>
  );
}

function JoinBody() {
  return (
    <>
      {JOIN_BRANCH_HANDLES.map((id, i) => (
        <Handle
          key={id}
          type="target"
          id={id}
          position={Position.Top}
          style={{ left: `${((i + 1) / (JOIN_BRANCH_HANDLES.length + 1)) * 100}%` }}
        />
      ))}
      <Handle type="source" id="next" position={Position.Bottom} />
      <div className="logic-node__badge logic-node__badge--join">⋈ ожидание веток</div>
    </>
  );
}

// RU plural forms: 1 шаг / 2-4 шага / 5+ (incl. 11-14) шагов.
function stepWord(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "шаг";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "шага";
  return "шагов";
}

function BatchBody({ data }: { data: CanvasNodeData }) {
  const childCount = data.children?.length ?? 0;
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <Handle type="source" id="next" position={Position.Bottom} />
      <div className="logic-node__batch-container" data-testid="logic-node-batch-count">
        {childCount} {stepWord(childCount)} внутри
      </div>
    </>
  );
}

function UtilityBody({ icon }: { icon: string }) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <Handle type="source" id="next" position={Position.Bottom} />
      <div className="logic-node__badge logic-node__badge--utility">{icon}</div>
    </>
  );
}

function DefaultBody({ catalogKey }: { catalogKey: string }) {
  const ports = outputPortsFor(catalogKey);
  return (
    <>
      <Handle type="target" position={Position.Top} />
      {ports.length === 1 ? (
        <Handle type="source" id={ports[0]} position={Position.Bottom} />
      ) : (
        ports.map((port, i) => (
          <Handle
            key={port}
            type="source"
            id={port}
            position={Position.Bottom}
            style={{ left: `${((i + 1) / (ports.length + 1)) * 100}%` }}
          />
        ))
      )}
      {ports.length > 1 ? (
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-faint)" }}>
          {ports.map((port) => (
            <span key={port}>{port}</span>
          ))}
        </div>
      ) : null}
    </>
  );
}

const VARIANT_CLASS: Record<string, string> = {
  "logic.fork": "logic-node--fork",
  "logic.join": "logic-node--join",
  "logic.batch": "logic-node--batch",
  "logic.batch_status": "logic-node--utility",
  "logic.batch_list_pending": "logic-node--utility",
};

function bodyFor(data: CanvasNodeData): ReactNode {
  switch (data.catalogKey) {
    case "logic.fork":
      return <ForkBody />;
    case "logic.join":
      return <JoinBody />;
    case "logic.batch":
      return <BatchBody data={data} />;
    case "logic.batch_status":
      return <UtilityBody icon="◔" />;
    case "logic.batch_list_pending":
      return <UtilityBody icon="≡" />;
    default:
      return <DefaultBody catalogKey={data.catalogKey} />;
  }
}

export function LogicNode({ data, selected }: NodeProps<Node<CanvasNodeData>>) {
  const variant = VARIANT_CLASS[data.catalogKey] ?? "logic-node--default";
  return (
    <div className={variant} data-logic-kind={data.catalogKey}>
      <NodeShell
        category="logic"
        label={data.label}
        description={nodeDescription(data.catalogKey)}
        selected={selected}
        errorMessage={data.errorMessage}
      >
        {bodyFor(data)}
      </NodeShell>
    </div>
  );
}
