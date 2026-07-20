import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AutoForm } from "./AutoForm";
import type { JsonSchema } from "../../api/flowClient";

/** One field's schema wrapped in the object shape `AutoForm` reads off GET /catalog. */
function schemaFor(key: string, propSchema: JsonSchema, required = true): JsonSchema {
  return {
    type: "object",
    properties: { [key]: propSchema },
    required: required ? [key] : [],
  };
}

function form(schema: JsonSchema) {
  const onChange = vi.fn();
  render(<AutoForm schema={schema} values={{}} onChange={onChange} />);
  return onChange;
}

describe("AutoForm widget dispatch", () => {
  it("renders a slider for a number field hinted x-ui.widget=slider", () => {
    const onChange = form(
      schemaFor("count", {
        type: "integer",
        title: "Count",
        minimum: 1,
        maximum: 10,
        "x-ui": { widget: "slider", step: 1 },
      }),
    );
    const range = screen.getByRole("slider") as HTMLInputElement;
    fireEvent.change(range, { target: { value: "7" } });
    expect(onChange).toHaveBeenCalledWith("count", 7);
  });

  it("renders a textarea for a string field hinted x-ui.widget=textarea", () => {
    const onChange = form(
      schemaFor("note", { type: "string", title: "Note", "x-ui": { widget: "textarea" } }),
    );
    const textarea = screen.getByRole("textbox");
    expect(textarea.tagName).toBe("TEXTAREA");
    fireEvent.change(textarea, { target: { value: "hello" } });
    expect(onChange).toHaveBeenCalledWith("note", "hello");
  });

  it("renders a datetime-local input for a string field hinted x-ui.widget=datetime", () => {
    const onChange = form(
      schemaFor("at", { type: "string", title: "At", "x-ui": { widget: "datetime" } }),
    );
    const input = screen.getByLabelText(/^At/) as HTMLInputElement;
    expect(input.type).toBe("datetime-local");
    fireEvent.change(input, { target: { value: "2026-01-01T12:00" } });
    expect(onChange).toHaveBeenCalledWith("at", "2026-01-01T12:00");
  });

  it("renders a radio group for an enum field hinted x-ui.widget=radio", () => {
    const onChange = form(
      schemaFor("mode", {
        type: "string",
        title: "Mode",
        enum: ["fast", "slow"],
        "x-ui": { widget: "radio" },
      }),
    );
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    fireEvent.click(screen.getByRole("radio", { name: "slow" }));
    expect(onChange).toHaveBeenCalledWith("mode", "slow");
  });

  it("renders a multiselect for an enum field hinted x-ui.widget=multiselect", () => {
    const onChange = form(
      schemaFor("tags", {
        type: "string",
        title: "Tags",
        enum: ["a", "b"],
        "x-ui": { widget: "multiselect" },
      }),
    );
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(2);
    fireEvent.click(checkboxes[0]);
    expect(onChange).toHaveBeenCalledWith("tags", JSON.stringify(["a"]));
  });

  it("still renders a plain text field when no widget hint is present", () => {
    const onChange = form(schemaFor("plain", { type: "string", title: "Plain" }));
    const input = screen.getByLabelText(/^Plain/) as HTMLInputElement;
    expect(input.type).toBe("text");
    fireEvent.change(input, { target: { value: "x" } });
    expect(onChange).toHaveBeenCalledWith("plain", "x");
  });
});
