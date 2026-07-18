import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useDynamicMethods } from "./useDynamicMethods";
import * as flowClient from "../../api/flowClient";
import type { CatalogNode } from "../../api/flowClient";

describe("useDynamicMethods", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts in the loading state", () => {
    vi.spyOn(flowClient, "fetchCatalog").mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useDynamicMethods());
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("groups dotted keys by facade and buckets dot-less keys by category", async () => {
    const nodes: CatalogNode[] = [
      { key: "market.bump", category: "action", input_schema: {}, output_schema: {}, idempotent: true, capabilities: [] },
      { key: "market.reprice", category: "action", input_schema: {}, output_schema: {}, idempotent: false, capabilities: [] },
      { key: "condition", category: "logic", input_schema: {}, output_schema: {}, idempotent: true, capabilities: [] },
    ];
    vi.spyOn(flowClient, "fetchCatalog").mockResolvedValue(nodes);

    const { result } = renderHook(() => useDynamicMethods());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.facades.market).toHaveLength(2);
    expect(result.current.facades.logic).toEqual([nodes[2]]);
    expect(result.current.error).toBeNull();
  });

  it("surfaces a fetch failure as an error and clears loading", async () => {
    vi.spyOn(flowClient, "fetchCatalog").mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useDynamicMethods());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("network down");
    expect(result.current.facades).toEqual({});
  });

  it("ignores a late resolution after unmount", async () => {
    let resolve!: (nodes: CatalogNode[]) => void;
    vi.spyOn(flowClient, "fetchCatalog").mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );

    const { result, unmount } = renderHook(() => useDynamicMethods());
    unmount();
    await act(async () => {
      resolve([]);
    });

    expect(result.current.loading).toBe(true);
  });
});
