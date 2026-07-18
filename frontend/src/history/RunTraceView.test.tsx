import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunTraceView } from "./RunTraceView";
import * as flowClient from "../api/flowClient";
import type { RunTraceStep, StepCompletedEvent } from "../api/flowClient";

describe("RunTraceView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when the trace has no steps", async () => {
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps: [] });
    render(<RunTraceView runId="r1" />);
    expect(await screen.findByText(/шагов в трассировке нет/i)).toBeInTheDocument();
  });

  it("renders each step with its display label, duration, args and result", async () => {
    const steps: RunTraceStep[] = [
      {
        node_id: "market.bump",
        node_type: "market.bump",
        args: { lot_id: 42 },
        result: { ok: true },
        duration_ms: 123,
        started_at: "2026-01-01T10:00:00Z",
      },
    ];
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps });
    render(<RunTraceView runId="r1" />);

    expect(await screen.findByText("Поднять лот")).toBeInTheDocument();
    expect(screen.getByText("123 мс")).toBeInTheDocument();
    expect(screen.getByText(/"lot_id": 42/)).toBeInTheDocument();
    expect(screen.getByText(/"ok": true/)).toBeInTheDocument();
  });

  it("groups fork/join steps sharing a branch_id into distinct visual lanes", async () => {
    const steps: RunTraceStep[] = [
      { node_id: "fork", node_type: "fork", args: {}, result: {}, duration_ms: 1, started_at: "2026-01-01T10:00:00Z" },
      {
        node_id: "action",
        node_type: "action",
        args: {},
        result: {},
        duration_ms: 5,
        started_at: "2026-01-01T10:00:01Z",
        branch_id: "a",
      },
      {
        node_id: "action",
        node_type: "action",
        args: {},
        result: {},
        duration_ms: 6,
        started_at: "2026-01-01T10:00:01Z",
        branch_id: "b",
      },
      { node_id: "join", node_type: "join", args: {}, result: {}, duration_ms: 2, started_at: "2026-01-01T10:00:02Z" },
    ];
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps });
    render(<RunTraceView runId="r1" />);

    await screen.findByText("Разветвление");
    expect(screen.getByText("Ветка a")).toBeInTheDocument();
    expect(screen.getByText("Ветка b")).toBeInTheDocument();
    expect(document.querySelectorAll(".run-trace-view__group--branch")).toHaveLength(2);
  });

  it("surfaces a load failure as an error message", async () => {
    vi.spyOn(flowClient, "fetchRunTrace").mockRejectedValue(new Error("trace boom"));
    render(<RunTraceView runId="r1" />);
    expect(await screen.findByText("trace boom")).toBeInTheDocument();
  });

  function mockStream(): { unsubscribe: ReturnType<typeof vi.fn>; emit: (e: StepCompletedEvent) => void } {
    const unsubscribe = vi.fn();
    let onEvent: (e: StepCompletedEvent) => void = () => undefined;
    // streamRun mints a stream token before connecting, so it resolves to the unsubscribe rather
    // than returning it.
    vi.spyOn(flowClient, "streamRun").mockImplementation(async (_runId, cb) => {
      onEvent = cb as (e: StepCompletedEvent) => void;
      return unsubscribe;
    });
    return { unsubscribe, emit: (e) => onEvent(e) };
  }

  function stepCompleted(overrides: Partial<StepCompletedEvent> = {}): StepCompletedEvent {
    return {
      type: "step_completed",
      event_id: "evt-1",
      occurred_at: "2026-01-01T10:00:00Z",
      run_id: "r1",
      node_id: "n1",
      node_type: "market.bump",
      iteration_key: null,
      duration_ms: 42,
      ...overrides,
    };
  }

  it("appends a step_completed SSE event as a new live row in live mode", async () => {
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps: [] });
    const { emit } = mockStream();
    render(<RunTraceView runId="r1" live />);

    await screen.findByText(/шагов в трассировке нет/i);
    act(() => emit(stepCompleted()));

    expect(await screen.findByText("Поднять лот")).toBeInTheDocument();
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(screen.getByText("42 мс")).toBeInTheDocument();
  });

  it("dedups a live event that matches a step already present in the static trace", async () => {
    const steps: RunTraceStep[] = [
      { node_id: "n1", node_type: "market.bump", args: { lot_id: 1 }, result: { ok: true }, duration_ms: 10, started_at: "2026-01-01T10:00:00Z" },
    ];
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps });
    const { emit } = mockStream();
    render(<RunTraceView runId="r1" live />);

    await screen.findByText("Поднять лот");
    act(() => emit(stepCompleted()));

    expect(screen.queryByText("live")).not.toBeInTheDocument();
    expect(screen.getAllByText("Поднять лот")).toHaveLength(1);
  });

  it("unsubscribes the SSE stream on unmount", async () => {
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps: [] });
    const { unsubscribe } = mockStream();
    const { unmount } = render(<RunTraceView runId="r1" live />);
    await screen.findByText(/шагов в трассировке нет/i);

    unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("unsubscribes and re-subscribes when runId changes", async () => {
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps: [] });
    const { unsubscribe } = mockStream();
    const { rerender } = render(<RunTraceView runId="r1" live />);
    await screen.findByText(/шагов в трассировке нет/i);

    rerender(<RunTraceView runId="r2" live />);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    await screen.findByText(/шагов в трассировке нет/i);
  });

  it("unsubscribes when live flips to false", async () => {
    vi.spyOn(flowClient, "fetchRunTrace").mockResolvedValue({ run_id: "r1", steps: [] });
    const { unsubscribe } = mockStream();
    const { rerender } = render(<RunTraceView runId="r1" live />);
    await screen.findByText(/шагов в трассировке нет/i);

    rerender(<RunTraceView runId="r1" live={false} />);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });
});
