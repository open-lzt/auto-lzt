import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchFlows,
  fetchRunTrace,
  getApiKey,
  setApiKey,
  streamRun,
  type LogEvent,
  type StepCompletedEvent,
} from "./flowClient";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("setApiKey / getApiKey", () => {
  afterEach(() => {
    sessionStorage.clear();
  });

  it("persists the key to sessionStorage and returns it via getApiKey", () => {
    setApiKey("secret-key");
    expect(getApiKey()).toBe("secret-key");
    expect(sessionStorage.getItem("lzt-flow.api-key")).toBe("secret-key");
  });
});

describe("request()", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ id: "f1", name: "Flow 1" }]));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("sends X-API-Key when a key is set", async () => {
    setApiKey("secret-key");
    await fetchFlows();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("secret-key");
  });

  it("shapes the bare trace list into {run_id, steps} with the view's field names", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse([
        {
          node_id: "n1",
          node_type: "market.bump",
          iteration_key: "lot-7",
          inputs: { item_id: 1 },
          output: { ok: true },
          duration_ms: 12,
          started_at: "2026-01-01T00:00:00Z",
          completed_at: "2026-01-01T00:00:01Z",
        },
      ]),
    );

    const trace = await fetchRunTrace("r1");

    expect(trace.run_id).toBe("r1");
    expect(trace.steps).toEqual([
      {
        node_id: "n1",
        node_type: "market.bump",
        args: { item_id: 1 },
        result: { ok: true },
        duration_ms: 12,
        started_at: "2026-01-01T00:00:00Z",
        branch_id: "lot-7",
      },
    ]);
  });

  it("renames the wire's flow_id to id so the UI never sees an undefined identifier", async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ flow_id: "f1", name: "Flow 1" }]));

    await expect(fetchFlows()).resolves.toEqual([{ id: "f1", name: "Flow 1" }]);
  });
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  close(): void {
    this.closed = true;
  }
  emit(data: string): void {
    this.onmessage?.({ data } as MessageEvent<string>);
  }
}

describe("streamRun", () => {
  // streamRun now spends the API key at POST /runs/{id}/stream-token and puts the returned
  // one-minute token in the URL — EventSource cannot send the key header itself. So subscribing
  // takes a round trip, and these tests await it.
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ token: "1799999999.deadbeef", expires_in: 60 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("authorizes the stream with a token instead of a header", async () => {
    await streamRun("run-1", () => {});

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/run-1/stream-token");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(FakeEventSource.instances[0].url).toBe(
      "/api/runs/run-1/stream?token=1799999999.deadbeef",
    );
  });

  it("parses and delivers well-formed event frames", async () => {
    const received: (StepCompletedEvent | LogEvent)[] = [];
    await streamRun("run-1", (e) => received.push(e));

    const source = FakeEventSource.instances[0];
    const stepEvent: StepCompletedEvent = {
      type: "step_completed",
      event_id: "evt-1",
      occurred_at: "2026-01-01T10:00:00Z",
      run_id: "run-1",
      node_id: "n1",
      node_type: "market.bump",
      iteration_key: null,
      duration_ms: 12,
    };
    source.emit(JSON.stringify(stepEvent));

    expect(received).toEqual([stepEvent]);
  });

  it("skips a malformed frame without throwing", async () => {
    const received: (StepCompletedEvent | LogEvent)[] = [];
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {});
    await streamRun("run-1", (e) => received.push(e));

    const source = FakeEventSource.instances[0];
    expect(() => source.emit("not json")).not.toThrow();

    expect(received).toEqual([]);
    expect(consoleErr).toHaveBeenCalled();
    consoleErr.mockRestore();
  });

  it("closes the EventSource when unsubscribed", async () => {
    const unsubscribe = await streamRun("run-1", () => {});
    const source = FakeEventSource.instances[0];
    expect(source.closed).toBe(false);
    unsubscribe();
    expect(source.closed).toBe(true);
  });

  it("opens no connection at all when the token is refused", async () => {
    // The gap this closes: /stream used to need no credential. If the token endpoint says no, the
    // right outcome is no stream — not a stream opened without one.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ code: "ERR-1010", message: "Unauthorized", request_id: "" }, 401)),
    );

    await expect(streamRun("run-1", () => {})).rejects.toThrow();
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
