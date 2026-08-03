import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import type { CanvasNodeData } from "../canvasTypes";
import "./template-boundary-node.css";

const KIND_LABEL: Record<"input" | "output", string> = {
  input: "Вход",
  output: "Выход",
};

const COPIED_FEEDBACK_MS = 900;

/** The `{{param.NAME}}` text is the only way to wire an input marker into a consuming field; saving fails without it. */
export function TemplateBoundaryNode({ data, selected }: NodeProps<Node<CanvasNodeData>>) {
  const kind = data.boundaryKind ?? "input";
  const name = data.paramName?.trim() || "(без имени)";
  const placeholder = `{{param.${name}}}`;
  const [copied, setCopied] = useState(false);

  function copyPlaceholder(): void {
    void navigator.clipboard?.writeText(placeholder);
    setCopied(true);
    window.setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
  }

  return (
    <div
      className={`boundary-node boundary-node--${kind}${selected ? " boundary-node--selected" : ""}`}
      data-boundary-kind={kind}
    >
      {kind === "output" ? <Handle type="target" position={Position.Left} /> : null}
      <span className="boundary-node__kind">{KIND_LABEL[kind]}</span>
      <span className="boundary-node__name">{name}</span>
      {kind === "input" ? (
        <button
          type="button"
          className="boundary-node__placeholder"
          onClick={copyPlaceholder}
          title="Вставьте это в поле блока, который принимает параметр"
        >
          {copied ? "скопировано" : placeholder}
        </button>
      ) : null}
      {kind === "input" ? <Handle type="source" position={Position.Right} /> : null}
    </div>
  );
}
