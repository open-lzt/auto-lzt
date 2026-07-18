import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ParamSurface } from "./ParamSurface";
import type { AccountRef } from "./ParamSurface";
import type { ParamSpec, ParamValue } from "./paramTypes";

function surface(
  params: ParamSpec[],
  values: Record<string, ParamValue> = {},
  accounts: AccountRef[] | undefined = undefined,
) {
  const onChange = vi.fn();
  render(
    <ParamSurface params={params} values={values} onChange={onChange} accounts={accounts} />,
  );
  return onChange;
}

describe("ParamSurface", () => {
  it("shows an empty message when there are no params", () => {
    surface([]);
    expect(screen.getByText(/no configurable parameters/i)).toBeInTheDocument();
  });

  it("renders a labelled slider and emits a numeric value", () => {
    const onChange = surface([
      { key: "count", label: "Купить аккаунтов", control: "slider", required: true, minimum: 1, maximum: 10 },
    ]);
    expect(screen.getByText("Купить аккаунтов")).toBeInTheDocument();
    const range = screen.getByRole("slider") as HTMLInputElement;
    fireEvent.change(range, { target: { value: "7" } });
    expect(onChange).toHaveBeenCalledWith("count", 7);
  });

  it("renders a category picker with the market categories", () => {
    surface([{ key: "cat", label: "Категория", control: "category_picker", required: true }]);
    expect(screen.getByRole("option", { name: "Steam" })).toBeInTheDocument();
  });

  it("renders an account picker from provided accounts", () => {
    surface(
      [{ key: "acc", label: "Аккаунт", control: "account_picker", required: true }],
      {},
      [{ id: "a1", label: "main" }],
    );
    expect(screen.getByRole("option", { name: "main" })).toBeInTheDocument();
  });

  it("shows a validation error for a required-empty field", () => {
    surface([{ key: "count", label: "Count", control: "number", required: true }]);
    expect(screen.getByText("Required")).toBeInTheDocument();
  });

  it("renders a description under the label", () => {
    surface([
      { key: "d", label: "Delay", control: "delay", required: false, description: "seconds between buys" },
    ]);
    expect(screen.getByText("seconds between buys")).toBeInTheDocument();
  });

  it("renders a radio group and emits the chosen value", () => {
    const onChange = surface([
      {
        key: "mode",
        label: "Mode",
        control: "radio",
        required: true,
        options: [
          { value: "fast", label: "Fast" },
          { value: "slow", label: "Slow" },
        ],
      },
    ]);
    fireEvent.click(screen.getByRole("radio", { name: "Slow" }));
    expect(onChange).toHaveBeenCalledWith("mode", "slow");
  });

  it("renders a textarea", () => {
    surface([{ key: "note", label: "Note", control: "textarea", required: false }]);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("groups params under section headings", () => {
    surface([
      { key: "a", label: "A", control: "text", required: false, group: "Basics" },
      { key: "b", label: "B", control: "text", required: false, group: "Advanced" },
    ]);
    expect(screen.getByRole("heading", { name: "Basics" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Advanced" })).toBeInTheDocument();
  });

  it("hides a param until its controlling field matches", () => {
    const params: ParamSpec[] = [
      { key: "mode", label: "Mode", control: "text", required: true },
      {
        key: "detail",
        label: "Detail",
        control: "text",
        required: true,
        visible_if: { field: "mode", equals: "advanced" },
      },
    ];
    const { rerender } = render(
      <ParamSurface params={params} values={{ mode: "basic" }} onChange={vi.fn()} />,
    );
    expect(screen.queryByText("Detail")).not.toBeInTheDocument();
    rerender(<ParamSurface params={params} values={{ mode: "advanced" }} onChange={vi.fn()} />);
    expect(screen.getByText("Detail")).toBeInTheDocument();
  });
});
