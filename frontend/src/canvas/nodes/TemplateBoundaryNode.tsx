import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import type { CanvasNodeData } from "../canvasTypes";
import "./template-boundary-node.css";

const KIND_LABEL: Record<"input" | "output", string> = {
  input: "Вход",
  output: "Выход",
};

const COPIED_FEEDBACK_MS = 900;

/** Authoring-time-only marker for one declared parameter on a composite template's surface —
 * it has no catalogKey/backend behaviour of its own. AuthoringMode reads `data.boundaryKind` /
 * `data.paramName` off it when assembling the template's `inputs`/`outputs` on save. Pill shape
 * (not the rectangular NodeShell chrome shared by Trigger/Action/Logic) so it reads as a
 * boundary, not a graph step. An output marker takes one incoming edge from the internal node
 * whose result it exposes; an input marker exposes a source handle for the operator's own
 * reference, but the actual wiring is a `{{param.NAME}}` literal typed into a consuming node's
 * field (composites have no data-flow edges, only control-flow ones — see AuthoringMode). Since
 * that literal is the ONLY way to connect an input — and saving fails without it — the marker
 * shows the exact text and copies it on click. */
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
