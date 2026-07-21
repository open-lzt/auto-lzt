import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HistoryPanel } from "./HistoryPanel";
import * as flowClient from "../api/flowClient";
import type { RunSummary } from "../api/flowClient";

describe("HistoryPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no runs", async () => {
    vi.spyOn(flowClient, "fetchRunHistory").mockResolvedValue([]);
    render(<HistoryPanel flowId="flow-1" />);
    expect(await screen.findByText(/запусков ещё не было/i)).toBeInTheDocument();
  });

  it("renders each run with its RU status label and timestamps", async () => {
    const runs: RunSummary[] = [
      { run_id: "r1", status: "completed", started_at: "2026-01-01T10:00:00Z", finished_at: "2026-01-01T10:00:05Z", duration_ms: 5000, error: null, failed_node_id: null },
      { run_id: "r2", status: "failed", started_at: "2026-01-02T10:00:00Z", finished_at: null, duration_ms: null, error: null, failed_node_id: null },
    ];
    vi.spyOn(flowClient, "fetchRunHistory").mockResolvedValue(runs);
    render(<HistoryPanel flowId="flow-1" />);

    expect(await screen.findByText("Завершён")).toBeInTheDocument();
    expect(screen.getByText("Ошибка")).toBeInTheDocument();
  });

  it("surfaces a load failure as an error message", async () => {
    vi.spyOn(flowClient, "fetchRunHistory").mockRejectedValue(new Error("boom"));
    render(<HistoryPanel flowId="flow-1" />);
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("selecting a run shows its trace in the detail pane", async () => {
    vi.spyOn(flowClient, "fetchRunHistory").mockResolvedValue([
      { run_id: "r1", status: "completed", started_at: "2026-01-01T10:00:00Z", finished_at: null, duration_ms: null, error: null, failed_node_id: null },
    ]);
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({
      run_id: "r1",
      steps: [{ node_id: "n1", node_type: "market.bump", args: {}, result: {}, duration_ms: 12, started_at: "2026-01-01T10:00:00Z" }],
    });

    render(<HistoryPanel flowId="flow-1" />);
    const row = await screen.findByText("Завершён");
    fireEvent.click(row.closest("button")!);

    await waitFor(() => expect(flowClient.fetchRunTrace).toHaveBeenCalledWith("r1"));
    expect(await screen.findByText("Поднять лот")).toBeInTheDocument();
  });

  // The bug this guards: the server sent `error` and `failed_node_id`, RunSummary did not declare
  // them, and the panel rendered a red «Ошибка» badge with the reason nowhere on screen — the exact
  // question this panel exists to answer. A missing field is silent, so the guard has to be a test.
  it("shows why a failed run stopped, and at which step", async () => {
    vi.spyOn(flowClient, "fetchRunHistory").mockResolvedValue([
      {
        run_id: "r1",
        status: "failed",
        started_at: "2026-01-01T10:00:00Z",
        finished_at: null,
        duration_ms: 398,
        error: "NoAvailableAccount('no available account for tenant 0000')",
        failed_node_id: "bump",
      },
    ]);
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps: [] });

    render(<HistoryPanel flowId="flow-1" />);
    fireEvent.click((await screen.findByText("Ошибка")).closest("button")!);

    expect(await screen.findByText(/NoAvailableAccount/)).toBeInTheDocument();
    expect(screen.getByText(/на шаге «bump»/)).toBeInTheDocument();
  });

  it("reloads history when flowId changes", async () => {
    const fetchMock = vi.spyOn(flowClient, "fetchRunHistory").mockResolvedValue([]);
    const { rerender } = render(<HistoryPanel flowId="flow-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("flow-1"));

    rerender(<HistoryPanel flowId="flow-2" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("flow-2"));
  });
});
