import { render } from "@testing-library/react";
import { ReactFlowProvider, type Node, type NodeProps } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { TemplateBoundaryNode } from "./TemplateBoundaryNode";
import type { CanvasNodeData } from "../canvasTypes";

function renderBoundaryNode(data: CanvasNodeData) {
  const props = {
    id: "b1",
    data,
    selected: false,
    type: "templateBoundary",
    dragging: false,
    zIndex: 0,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    deletable: true,
    draggable: true,
    selectable: true,
  } as unknown as NodeProps<Node<CanvasNodeData>>;

  return render(
    <ReactFlowProvider>
      <TemplateBoundaryNode {...props} />
    </ReactFlowProvider>,
  );
}

function baseData(extra: Partial<CanvasNodeData> = {}): CanvasNodeData {
  return { catalogKey: "boundary", category: "action", label: "param", values: {}, ...extra };
}

describe("TemplateBoundaryNode", () => {
  it("renders an input marker distinctly from an output marker", () => {
    const { container: inputContainer } = renderBoundaryNode(
      baseData({ boundaryKind: "input", paramName: "amount" }),
    );
    expect(inputContainer.querySelector('[data-boundary-kind="input"]')).toBeInTheDocument();
    expect(inputContainer.querySelector(".boundary-node--input")).toBeInTheDocument();
    expect(inputContainer.textContent).toContain("amount");

    const { container: outputContainer } = renderBoundaryNode(
      baseData({ boundaryKind: "output", paramName: "result" }),
    );
    expect(outputContainer.querySelector('[data-boundary-kind="output"]')).toBeInTheDocument();
    expect(outputContainer.querySelector(".boundary-node--output")).toBeInTheDocument();
    expect(outputContainer.textContent).toContain("result");
  });

  it("falls back to a placeholder when no param name is set yet", () => {
    const { container } = renderBoundaryNode(baseData({ boundaryKind: "input", paramName: "" }));
    expect(container.textContent).toContain("без имени");
  });
});
