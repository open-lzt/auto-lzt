import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ParamSpec } from "../../api/paramSpec";
import { RunParamsDialog } from "./RunParamsDialog";

function param(over: Partial<ParamSpec> = {}): ParamSpec {
  return { key: "price", label: "Цена", control: "number", required: false, ...over };
}

describe("RunParamsDialog", () => {
  it("prefills declared defaults so the common case is one click", () => {
    const onSubmit = vi.fn();
    render(<RunParamsDialog params={[param({ default: 100 })]} onSubmit={onSubmit} onCancel={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    expect(onSubmit).toHaveBeenCalledWith({ price: 100 });
  });

  it("blocks the run on a required param with no value, matching what resolve_params would say", () => {
    render(<RunParamsDialog params={[param({ required: true })]} onSubmit={vi.fn()} onCancel={() => {}} />);
    expect(screen.getByRole("button", { name: "Запустить" })).toBeDisabled();
  });

  it("sends only what was set — an untouched optional param falls back to the server's default", () => {
    const onSubmit = vi.fn();
    render(
      <RunParamsDialog
        params={[param(), param({ key: "note", label: "Заметка", control: "text" })]}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    expect(onSubmit).toHaveBeenCalledWith({});
  });

  it("passes an entered value through", () => {
    const onSubmit = vi.fn();
    render(
      <RunParamsDialog
        params={[param({ key: "note", label: "Заметка", control: "text" })]}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Заметка/), { target: { value: "привет" } });
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    expect(onSubmit).toHaveBeenCalledWith({ note: "привет" });
  });
});
