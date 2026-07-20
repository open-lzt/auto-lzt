import { render, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTaskStream } from "./useTaskStream";
import type { TaskEvent } from "../api/tasksClient";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  emit(data: string, lastEventId = ""): void {
    this.onmessage?.({ data, lastEventId } as MessageEvent<string>);
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }
}

const tokens: string[] = [];

vi.mock("../api/tasksClient", async () => {
  const actual = await vi.importActual<typeof import("../api/tasksClient")>("../api/tasksClient");
  return {
    ...actual,
    createTaskStreamToken: vi.fn(async () => {
      const token = `tok-${tokens.length}`;
      tokens.push(token);
      return token;
    }),
  };
});

function Probe({ onEvent }: { onEvent: (e: TaskEvent) => void }) {
  const state = useTaskStream(onEvent);
  return <span data-testid="state">{state}</span>;
}

function event(flowId: string): string {
  return JSON.stringify({ type: "task", reason: "run_finished", flow_id: flowId });
}

describe("useTaskStream", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    tokens.length = 0;
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function mounted(onEvent: (e: TaskEvent) => void = () => {}) {
    const utils = render(<Probe onEvent={onEvent} />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    return utils;
  }

  it("opens exactly ONE connection, and keeps it open across a series of events", async () => {
    // The assertion with teeth. Counting only at mount would pass against a hook that tears the
    // connection down and rebuilds it on every event — which is the failure this design exists to
    // prevent, and which is invisible in a single-event test.
    const seen: TaskEvent[] = [];
    await mounted((e) => seen.push(e));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.open();
      for (let i = 0; i < 10; i += 1) source.emit(event(`flow-${i}`), `id-${i}`);
    });

    expect(seen).toHaveLength(10);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(source.closed).toBe(false);
  });

  it("does not reconnect when the handler identity changes on every render", async () => {
    // A parent that passes an inline arrow — the overwhelmingly common case — re-renders with a new
    // function each time. If the hook depended on the handler, this alone would reconnect.
    const { rerender } = await mounted();

    for (let i = 0; i < 5; i += 1) {
      rerender(<Probe onEvent={() => undefined} />);
    }

    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("re-trades an expired token exactly once per failure, not in a loop", async () => {
    await mounted();
    const first = FakeEventSource.instances[0];

    await act(async () => {
      first.fail();
      // Longer than the hook's first backoff step, which is deliberately a real second.
      await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 4000 });
    });

    expect(first.closed).toBe(true);
    // A second token was minted for the reconnect, and exactly one — a hook that retried on a bare
    // timer would keep minting.
    expect(tokens).toEqual(["tok-0", "tok-1"]);
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("resumes from the last event id it actually saw", async () => {
    await mounted();
    const first = FakeEventSource.instances[0];

    act(() => {
      first.open();
      first.emit(event("flow-a"), "id-42");
    });
    await act(async () => {
      first.fail();
      // Longer than the hook's first backoff step, which is deliberately a real second.
      await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 4000 });
    });

    // Carried in the query string because EventSource cannot set a Last-Event-ID header on a
    // connection the client opens itself — without it, the reconnect would silently skip the gap.
    expect(FakeEventSource.instances[1].url).toContain("last_event_id=id-42");
  });

  // NOT covered: giving up after MAX_RECONNECTS. Driving it would mean waiting out the real
  // escalating backoff (~40s), and the no-loop property it protects is already pinned by the
  // "exactly once per failure" test above.

  it("closes the connection on unmount", async () => {
    const { unmount } = await mounted();
    const source = FakeEventSource.instances[0];

    unmount();

    expect(source.closed).toBe(true);
  });

  it("survives a malformed frame without killing the stream", async () => {
    const seen: TaskEvent[] = [];
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    await mounted((e) => seen.push(e));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.emit("{not json");
      source.emit(event("flow-ok"));
    });

    expect(seen).toHaveLength(1);
    expect(source.closed).toBe(false);
  });
});
