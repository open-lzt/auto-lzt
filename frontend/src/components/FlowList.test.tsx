import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FlowList } from "./FlowList";
import * as flowClient from "../api/flowClient";
import type { FlowSpec, FlowSummary, ImportError } from "../api/flowClient";

// jsdom doesn't implement File/Blob#text() (real browsers do) — polyfill via FileReader so the
// component's `await file.text()` path is exercised the same way it runs in production.
if (!File.prototype.text) {
  File.prototype.text = function (this: File) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}

function jsonFile(content: unknown, name = "flow.json"): File {
  return new File([JSON.stringify(content)], name, { type: "application/json" });
}

const FLOW_SPEC: FlowSpec = { name: "Flow One", nodes: [], entry_node_id: "n1" };

describe("FlowList", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no flows", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);
    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    expect(await screen.findByText(/здесь пока пусто/i)).toBeInTheDocument();
  });

  it("renders each flow and highlights the active one", async () => {
    const flows: FlowSummary[] = [
      { id: "f1", name: "Flow One" },
      { id: "f2", name: "Flow Two" },
    ];
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue(flows);
    render(<FlowList activeFlowId="f2" onSelect={vi.fn()} onCreateNew={vi.fn()} />);

    const active = await screen.findByText("Flow Two");
    expect(active.closest("li")).toHaveClass("flow-list__item--active");
    expect(screen.getByText("Flow One").closest("li")).not.toHaveClass("flow-list__item--active");
  });

  it("calls onSelect with the flow id when a name is clicked", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([{ id: "f1", name: "Flow One" }]);
    const onSelect = vi.fn();
    render(<FlowList activeFlowId={null} onSelect={onSelect} onCreateNew={vi.fn()} />);

    fireEvent.click(await screen.findByText("Flow One"));
    expect(onSelect).toHaveBeenCalledWith("f1");
  });

  it("calls onCreateNew when the new-flow button is clicked", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);
    const onCreateNew = vi.fn();
    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={onCreateNew} />);

    await screen.findByText(/здесь пока пусто/i);
    fireEvent.click(screen.getByText("+ новый"));
    expect(onCreateNew).toHaveBeenCalled();
  });

  it("deletes a flow and refetches the list", async () => {
    const fetchMock = vi
      .spyOn(flowClient, "fetchFlows")
      .mockResolvedValueOnce([{ id: "f1", name: "Flow One" }])
      .mockResolvedValueOnce([]);
    const deleteMock = vi.spyOn(flowClient, "deleteFlow").mockResolvedValue(undefined);

    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText("Удалить Flow One"));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("f1"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText(/здесь пока пусто/i)).toBeInTheDocument());
  });

  it("surfaces a load failure as an error message", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockRejectedValue(new Error("boom"));
    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("exports a flow as a downloaded JSON file", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([{ id: "f1", name: "Flow One" }]);
    const exportMock = vi.spyOn(flowClient, "exportFlow").mockResolvedValue(FLOW_SPEC);
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText("Экспортировать Flow One"));

    await waitFor(() => expect(exportMock).toHaveBeenCalledWith("f1"));
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("surfaces an export failure as an error message", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([{ id: "f1", name: "Flow One" }]);
    vi.spyOn(flowClient, "exportFlow").mockRejectedValue(new Error("экспорт не удался"));

    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText("Экспортировать Flow One"));

    expect(await screen.findByText("экспорт не удался")).toBeInTheDocument();
  });

  it("imports a valid flow file and refetches the list", async () => {
    const fetchMock = vi
      .spyOn(flowClient, "fetchFlows")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: "f1", name: "Flow One" }]);
    const importMock = vi
      .spyOn(flowClient, "importFlow")
      .mockResolvedValue({ ok: true, flowId: "f1" });

    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    await screen.findByText(/здесь пока пусто/i);

    const input = document.querySelector(".flow-list__import-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [jsonFile(FLOW_SPEC)] } });

    await waitFor(() => expect(importMock).toHaveBeenCalledWith(FLOW_SPEC));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("Flow One")).toBeInTheDocument());
  });

  it("renders a grouped error report when the backend rejects the import", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);
    const errors: ImportError[] = [
      { node_id: "n1", stage: "schema", message: "отсутствует обязательное поле" },
      { node_id: null, stage: "compile", message: "цикл в графе" },
    ];
    vi.spyOn(flowClient, "importFlow").mockResolvedValue({ ok: false, errors });

    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    await screen.findByText(/здесь пока пусто/i);

    const input = document.querySelector(".flow-list__import-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [jsonFile(FLOW_SPEC)] } });

    expect(await screen.findByText(/Не удалось импортировать флоу — 2 ошибок/)).toBeInTheDocument();
    expect(screen.getByText("отсутствует обязательное поле")).toBeInTheDocument();
    expect(screen.getByText("цикл в графе")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows a distinct parse error for malformed JSON and never calls importFlow", async () => {
    vi.spyOn(flowClient, "fetchFlows").mockResolvedValue([]);
    const importMock = vi.spyOn(flowClient, "importFlow");

    render(<FlowList activeFlowId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    await screen.findByText(/здесь пока пусто/i);

    const input = document.querySelector(".flow-list__import-input") as HTMLInputElement;
    const badFile = new File(["{not valid json"], "flow.json", { type: "application/json" });
    fireEvent.change(input, { target: { files: [badFile] } });

    expect(
      await screen.findByText("Файл повреждён или не является корректным JSON"),
    ).toBeInTheDocument();
    expect(importMock).not.toHaveBeenCalled();
  });
});
