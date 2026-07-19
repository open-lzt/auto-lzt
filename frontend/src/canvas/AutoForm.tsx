import type { JsonSchema, JsonSchemaUi } from "../api/flowClient";
import {
  Checkbox,
  DateTimePicker,
  Field,
  MultiSelect,
  RadioGroup,
  SelectField,
  Slider,
  TextArea,
  TextField,
} from "./ui/controls";
import "./autoform.css";

type FieldValue = string | number | boolean;

/** A number, or any prefix of a `{{param.name}}` template reference (matched while typing). */
const NUMBER_OR_PARAM_REF = /^(-?\d*\.?\d*|\{\{?p?a?r?a?m?\.?[\w.]*\}?\}?)$/;

interface FieldSpec {
  key: string;
  title: string;
  required: boolean;
  kind: "string" | "number" | "boolean" | "enum" | "unknown";
  enumValues?: (string | number)[];
  // x-ui.widget picks the control within a kind: radio/multiselect for enum, textarea/datetime
  // for string. "slider" is number-only, so it carries its own config below instead of here.
  widget?: JsonSchemaUi["widget"];
  slider?: { min: number; max: number; step: number; unit?: string };
}

// Pydantic v2 renders `X | None` as anyOf: [{type: X}, {type: "null"}] — unwrap to the real type
// so the form still renders a normal input instead of falling back to "unknown".
function resolveType(schema: JsonSchema): { type?: string; enumValues?: (string | number)[] } {
  if (schema.enum) return { type: schema.type, enumValues: schema.enum };
  if (schema.anyOf) {
    const real = schema.anyOf.find((s) => s.type && s.type !== "null");
    if (real) return resolveType(real);
  }
  return { type: schema.type };
}

function toFieldSpec(key: string, schema: JsonSchema, required: Set<string>): FieldSpec {
  const { type, enumValues } = resolveType(schema);
  const title = schema.title ?? key;
  const isRequired = required.has(key);
  const ui = schema["x-ui"];
  if (enumValues) {
    return { key, title, required: isRequired, kind: "enum", enumValues, widget: ui?.widget };
  }
  if (type === "integer" || type === "number") {
    const slider =
      ui?.widget === "slider"
        ? {
            min: schema.minimum ?? 0,
            max: schema.maximum ?? 100,
            step: ui.step ?? 1,
            unit: ui.unit,
          }
        : undefined;
    return { key, title, required: isRequired, kind: "number", slider };
  }
  if (type === "boolean") return { key, title, required: isRequired, kind: "boolean" };
  if (type === "string") return { key, title, required: isRequired, kind: "string", widget: ui?.widget };
  return { key, title, required: isRequired, kind: "unknown" };
}

interface AutoFormProps {
  schema: JsonSchema;
  values: Record<string, FieldValue>;
  onChange: (key: string, value: FieldValue) => void;
}

/** Renders one input per JSON-Schema property — the node's `input_schema` fetched from GET
 * /catalog. Supports string/number/boolean/enum, with `x-ui.widget` upgrading string to
 * textarea/datetime, enum to radio/multiselect, and number to slider; anything more exotic falls
 * back to a plain text field rather than silently dropping the parameter. */
export function AutoForm({ schema, values, onChange }: AutoFormProps) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const fields = Object.entries(properties).map(([key, propSchema]) => toFieldSpec(key, propSchema, required));

  if (fields.length === 0) {
    return <p className="autoform__empty">у этого блока нет параметров</p>;
  }

  return (
    <div className="autoform">
      {fields.map((field) => {
        const raw = values[field.key] === undefined ? "" : String(values[field.key]);
        if (field.kind === "boolean") {
          return (
            <label key={field.key} className="autoform__field autoform__field--inline">
              <span className="autoform__label">
                {field.title}
                {field.required ? <span className="autoform__required">*</span> : null}
              </span>
              <Checkbox checked={Boolean(values[field.key])} onChange={(v) => onChange(field.key, v)} />
            </label>
          );
        }
        return (
          <Field key={field.key} label={field.title} required={field.required}>
            {field.kind === "enum" && field.widget === "radio" ? (
              <RadioGroup
                name={field.key}
                value={String(values[field.key] ?? "")}
                onChange={(v) => onChange(field.key, v)}
                options={(field.enumValues ?? []).map((opt) => ({ value: String(opt), label: String(opt) }))}
              />
            ) : field.kind === "enum" && field.widget === "multiselect" ? (
              <MultiSelect
                value={raw}
                onChange={(v) => onChange(field.key, v)}
                options={(field.enumValues ?? []).map((opt) => ({ value: String(opt), label: String(opt) }))}
              />
            ) : field.kind === "enum" ? (
              <SelectField value={String(values[field.key] ?? "")} onChange={(v) => onChange(field.key, v)}>
                <option value="" disabled>
                  выберите…
                </option>
                {field.enumValues?.map((opt) => (
                  <option key={String(opt)} value={opt}>
                    {opt}
                  </option>
                ))}
              </SelectField>
            ) : field.kind === "number" && field.slider ? (
              <Slider
                value={Number(values[field.key] ?? field.slider.min)}
                onChange={(v) => onChange(field.key, v)}
                min={field.slider.min}
                max={field.slider.max}
                step={field.slider.step}
                unit={field.slider.unit}
              />
            ) : field.kind === "number" ? (
              // Value is kept as the raw string while typing — a live Number() coercion eats a
              // trailing "." and breaks decimal entry; buildFlowSpec coerces before sending.
              // A numeric field must also accept a partially-typed `{{param.x}}` reference: inside
              // a composite that literal IS how an input parameter is wired, so a digits-only
              // filter made every numeric parameter impossible to connect.
              <TextField
                inputMode="decimal"
                value={raw}
                onChange={(v) => {
                  if (v === "" || NUMBER_OR_PARAM_REF.test(v)) onChange(field.key, v);
                }}
              />
            ) : field.kind === "string" && field.widget === "textarea" ? (
              <TextArea value={raw} onChange={(v) => onChange(field.key, v)} />
            ) : field.kind === "string" && field.widget === "datetime" ? (
              <DateTimePicker value={raw} onChange={(v) => onChange(field.key, v)} />
            ) : (
              <TextField value={raw} onChange={(v) => onChange(field.key, v)} />
            )}
          </Field>
        );
      })}
    </div>
  );
}
