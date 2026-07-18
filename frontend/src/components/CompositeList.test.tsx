import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CompositeList } from "./CompositeList";
import * as flowClient from "../api/flowClient";
import type { CompositeSummary } from "../api/flowClient";

describe("CompositeList", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the empty state when there are no composites", async () => {
    vi.spyOn(flowClient, "listComposites").mockResolvedValue([]);
    render(<CompositeList activeCompositeId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    expect(await screen.findByText(/пока нет ни одного составного блока/i)).toBeInTheDocument();
  });

  it("renders each composite and highlights the active one", async () => {
    const composites: CompositeSummary[] = [
      { id: "c1", name: "Составной один" },
      { id: "c2", name: "Составной два" },
    ];
    vi.spyOn(flowClient, "listComposites").mockResolvedValue(composites);
    render(<CompositeList activeCompositeId="c2" onSelect={vi.fn()} onCreateNew={vi.fn()} />);

    const active = await screen.findByText("Составной два");
    expect(active.closest("li")).toHaveClass("composite-list__item--active");
    expect(screen.getByText("Составной один").closest("li")).not.toHaveClass("composite-list__item--active");
  });

  it("calls onSelect with the composite id when a name is clicked", async () => {
    vi.spyOn(flowClient, "listComposites").mockResolvedValue([{ id: "c1", name: "Составной один" }]);
    const onSelect = vi.fn();
    render(<CompositeList activeCompositeId={null} onSelect={onSelect} onCreateNew={vi.fn()} />);

    fireEvent.click(await screen.findByText("Составной один"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });

  it("calls onCreateNew when the new-composite button is clicked", async () => {
    vi.spyOn(flowClient, "listComposites").mockResolvedValue([]);
    const onCreateNew = vi.fn();
    render(<CompositeList activeCompositeId={null} onSelect={vi.fn()} onCreateNew={onCreateNew} />);

    await screen.findByText(/пока нет ни одного составного блока/i);
    fireEvent.click(screen.getByText("+ новый"));
    expect(onCreateNew).toHaveBeenCalled();
  });

  it("shows an error state when the fetch fails", async () => {
    vi.spyOn(flowClient, "listComposites").mockRejectedValue(new Error("сеть недоступна"));
    render(<CompositeList activeCompositeId={null} onSelect={vi.fn()} onCreateNew={vi.fn()} />);
    expect(await screen.findByText("сеть недоступна")).toBeInTheDocument();
  });
});
