import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthoringMode } from "./AuthoringMode";
import * as flowClient from "../api/flowClient";
import type { CatalogNode, CompositeDetail } from "../api/flowClient";

const CATALOG: CatalogNode[] = [
  { key: "market.bump", category: "action", input_schema: {}, output_schema: {}, idempotent: true, capabilities: [] },
];

function stubCatalog() {
  vi.spyOn(flowClient, "fetchCatalog").mockResolvedValue(CATALOG);
}

describe("AuthoringMode", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("blocks save with no internal nodes on a fresh composite", async () => {
    stubCatalog();
    const createMock = vi.spyOn(flowClient, "createComposite");
    render(<AuthoringMode compositeId={null} onSaved={vi.fn()} onCancel={vi.fn()} />);

    fireEvent.click(await screen.findByText("сохранить"));

    expect(await screen.findByText(/добавьте хотя бы один внутренний блок/i)).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it("saves a loaded composite whose input is referenced and output is connected", async () => {
    stubCatalog();
    const composite: CompositeDetail = {
      id: "c1",
      name: "Тестовый блок",
      nodes: [
        {
          id: "n1",
          type: "market.bump",
          inputs: { note: { literal: "{{param.amount}}" } },
          account_ref: null,
          edges: {},
          on_error: null,
        },
      ],
      entry_node_id: "n1",
      inputs: [{ name: "amount", output_port: null }],
      outputs: [{ name: "result", output_port: "n1.next" }],
      created_at: "2026-01-01T00:00:00Z",
    };
    vi.spyOn(flowClient, "getComposite").mockResolvedValue(composite);
    const createMock = vi.spyOn(flowClient, "createComposite").mockResolvedValue(composite);
    const onSaved = vi.fn();

    render(<AuthoringMode compositeId="c1" onSaved={onSaved} onCancel={vi.fn()} />);

    await screen.findByDisplayValue("Тестовый блок");
    fireEvent.click(screen.getByText("сохранить"));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    const request = createMock.mock.calls[0][0];
    expect(request.entry_node_id).toBe("n1");
    expect(request.inputs).toEqual([{ name: "amount", output_port: null }]);
    expect(request.outputs).toEqual([{ name: "result", output_port: "n1.next" }]);
    expect(onSaved).toHaveBeenCalledWith(composite);
  });

  it("blocks save when a loaded output marker has no producing edge", async () => {
    stubCatalog();
    const composite: CompositeDetail = {
      id: "c2",
      name: "Незавершённый блок",
      nodes: [
        { id: "n1", type: "market.bump", inputs: {}, account_ref: null, edges: {}, on_error: null },
      ],
      entry_node_id: "n1",
      inputs: [],
      outputs: [{ name: "result", output_port: null }],
      created_at: "2026-01-01T00:00:00Z",
    };
    vi.spyOn(flowClient, "getComposite").mockResolvedValue(composite);
    const createMock = vi.spyOn(flowClient, "createComposite");

    render(<AuthoringMode compositeId="c2" onSaved={vi.fn()} onCancel={vi.fn()} />);

    await screen.findByDisplayValue("Незавершённый блок");
    fireEvent.click(screen.getByText("сохранить"));

    expect(await screen.findByText(/Параметры не подключены: result/i)).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it("calls onCancel when the back button is clicked", async () => {
    stubCatalog();
    const onCancel = vi.fn();
    render(<AuthoringMode compositeId={null} onSaved={vi.fn()} onCancel={onCancel} />);

    fireEvent.click(await screen.findByLabelText("Назад к списку"));
    expect(onCancel).toHaveBeenCalled();
  });
});
