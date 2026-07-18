import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createComposite, getComposite, listComposites } from "./flowClient";
import type { CompositeDetail, CreateCompositeRequest } from "./flowClient";

const COMPOSITE: CompositeDetail = {
  id: "c1",
  name: "Составной блок",
  nodes: [{ id: "n1", type: "market.bump", inputs: {}, account_ref: null, edges: {}, on_error: null }],
  entry_node_id: "n1",
  inputs: [{ name: "amount", output_port: null }],
  outputs: [{ name: "result", output_port: "n1.next" }],
  created_at: "2026-01-01T00:00:00Z",
};

/** What the API actually puts on the wire: the identifier is `composite_id`, not `id`. */
const { id: _compositeId, ...COMPOSITE_REST } = COMPOSITE;
const COMPOSITE_WIRE = { composite_id: "c1", ...COMPOSITE_REST };

const REQUEST: CreateCompositeRequest = {
  name: COMPOSITE.name,
  nodes: COMPOSITE.nodes,
  entry_node_id: COMPOSITE.entry_node_id,
  inputs: COMPOSITE.inputs,
  outputs: COMPOSITE.outputs,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("flowClient composite endpoints", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("createComposite posts the request and returns the created composite", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(COMPOSITE_WIRE, 201));

    const result = await createComposite(REQUEST);

    expect(result).toEqual(COMPOSITE);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/composites/create",
      expect.objectContaining({ method: "POST", body: JSON.stringify(REQUEST) }),
    );
  });

  it("createComposite raises ApiError with the backend envelope on failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ code: "VALIDATION_ERROR", message: "имя обязательно", request_id: "r1" }, 400),
    );

    await expect(createComposite(REQUEST)).rejects.toThrow(ApiError);
  });

  it("listComposites trims the full response down to id+name", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse([COMPOSITE_WIRE]));

    const result = await listComposites();

    expect(result).toEqual([{ id: "c1", name: "Составной блок" }]);
  });

  it("listComposites surfaces a fetch failure as an ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ code: "INTERNAL", message: "сбой сервера", request_id: "r2" }, 500),
    );

    await expect(listComposites()).rejects.toThrow(ApiError);
  });

  it("getComposite fetches a single composite by id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(COMPOSITE_WIRE));

    const result = await getComposite("c1");

    expect(result).toEqual(COMPOSITE);
    expect(fetchMock).toHaveBeenCalledWith("/api/composites/c1", expect.anything());
  });
});
