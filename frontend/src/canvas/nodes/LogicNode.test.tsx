import { render } from "@testing-library/react";
import { ReactFlowProvider, type Node, type NodeProps } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { LogicNode } from "./LogicNode";
import type { CanvasNodeData } from "../canvasTypes";

function renderLogicNode(data: CanvasNodeData) {
  const props = {
    id: "n1",
    data,
    selected: false,
    type: "logic",
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
      <LogicNode {...props} />
    </ReactFlowProvider>,
  );
}

function baseData(catalogKey: string, extra: Partial<CanvasNodeData> = {}): CanvasNodeData {
  return { catalogKey, category: "logic", label: catalogKey, values: {}, ...extra };
}

describe("LogicNode", () => {
  it("renders a distinct fork marker for logic.fork", () => {
    const { container } = renderLogicNode(baseData("logic.fork"));
    expect(container.querySelector('[data-logic-kind="logic.fork"]')).toBeInTheDocument();
    expect(container.querySelector(".logic-node--fork")).toBeInTheDocument();
    expect(container.querySelector(".logic-node__badge--fork")).toBeInTheDocument();
  });

  it("renders a distinct join marker for logic.join", () => {
    const { container } = renderLogicNode(baseData("logic.join"));
    expect(container.querySelector('[data-logic-kind="logic.join"]')).toBeInTheDocument();
    expect(container.querySelector(".logic-node--join")).toBeInTheDocument();
    expect(container.querySelector(".logic-node__badge--join")).toBeInTheDocument();
  });

  it("renders a batch container showing the nested child count for logic.batch", () => {
    const { container, getByTestId } = renderLogicNode(
      baseData("logic.batch", {
        children: [
          { id: "c1", catalogKey: "market.bump", values: {} },
          { id: "c2", catalogKey: "market.reprice", values: {} },
        ],
      }),
    );
    expect(container.querySelector(".logic-node--batch")).toBeInTheDocument();
    expect(getByTestId("logic-node-batch-count").textContent).toBe("2 шага внутри");
  });

  it("renders zero children gracefully for an empty logic.batch", () => {
    const { getByTestId } = renderLogicNode(baseData("logic.batch"));
    expect(getByTestId("logic-node-batch-count").textContent).toBe("0 шагов внутри");
  });

  it("renders a utility badge for logic.batch_status", () => {
    const { container } = renderLogicNode(baseData("logic.batch_status"));
    expect(container.querySelector(".logic-node--utility")).toBeInTheDocument();
    expect(container.querySelector(".logic-node__badge--utility")).toBeInTheDocument();
  });

  it("renders a utility badge for logic.batch_list_pending", () => {
    const { container } = renderLogicNode(baseData("logic.batch_list_pending"));
    expect(container.querySelector(".logic-node--utility")).toBeInTheDocument();
    expect(container.querySelector(".logic-node__badge--utility")).toBeInTheDocument();
  });
});
