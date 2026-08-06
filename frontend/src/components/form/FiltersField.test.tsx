import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as catalogClient from "../../api/catalogClient";
import { FiltersField } from "./FiltersField";

const SCHEMA: catalogClient.CategoryFiltersDTO = {
  category: "steam",
  count: 4,
  json_schema: {
    type: "object",
    properties: {
      pmin: { type: "number", title: "pmin" },
      origin: { type: "string", enum: ["autoreg", "brute"], title: "origin" },
      nsb: { type: "boolean", title: "nsb" },
      obscure_one: { type: "integer", title: "obscure_one" },
    },
  },
  curated: ["pmin", "origin", "nsb"],
};

describe("FiltersField", () => {
  beforeEach(() => {
    vi.spyOn(catalogClient, "fetchCategoryFilters").mockResolvedValue(SCHEMA);
  });

  it("shows the curated filters and hides the rest behind a count", async () => {
    render(<FiltersField value="{}" onChange={vi.fn()} category="steam" />);

    expect(await screen.findByLabelText(/pmin/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/obscure_one/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Все фильтры \(1\)/ })).toBeInTheDocument();
  });

  it("reveals the rest when the disclosure is opened", async () => {
    render(<FiltersField value="{}" onChange={vi.fn()} category="steam" />);
    fireEvent.click(await screen.findByRole("button", { name: /Все фильтры/ }));
    expect(screen.getByLabelText(/obscure_one/)).toBeInTheDocument();
  });

  it("counts the filters set inside the collapsed section", async () => {
    // Without this, closing the disclosure hides the fact that the search is still narrowed.
    render(<FiltersField value='{"obscure_one": 5}' onChange={vi.fn()} category="steam" />);
    const toggle = await screen.findByRole("button", { name: /Все фильтры/ });
    expect(toggle).toHaveTextContent("1");
  });

  it("deletes a filter when it is cleared instead of sending an empty string", async () => {
    // An empty string is a VALUE: the backend would try to coerce it and reject the whole run.
    const onChange = vi.fn();
    render(<FiltersField value='{"pmin": 100}' onChange={onChange} category="steam" />);
    fireEvent.change(await screen.findByLabelText(/pmin/), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith("{}");
  });

  it("names filters left over from another category rather than dropping them", async () => {
    // They are still in the value the run would send, and the backend rejects the whole search.
    render(<FiltersField value='{"not_a_steam_filter": 1}' onChange={vi.fn()} category="steam" />);
    expect(await screen.findByText(/не из этой категории/)).toHaveTextContent("not_a_steam_filter");
  });

  it("falls back to raw editing when the value is not parseable", async () => {
    render(<FiltersField value="{oops" onChange={vi.fn()} category="steam" />);
    expect(await screen.findByText(/не разбирается как JSON/)).toBeInTheDocument();
  });

  it("asks for a category before fetching anything", async () => {
    render(<FiltersField value="{}" onChange={vi.fn()} category="" />);
    expect(screen.getByText(/сначала выберите категорию/)).toBeInTheDocument();
    await waitFor(() => expect(catalogClient.fetchCategoryFilters).not.toHaveBeenCalled());
  });
});
