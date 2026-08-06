import type { JsonSchema, JsonSchemaUi } from "../../api/catalogClient";
import {
  Checkbox,
  DateTimePicker,
  Field,
  MultiSelect,
  OptionPicker,
  RadioGroup,
  Slider,
  TextArea,
  TextField,
  type PickerOption,
} from "./controls";
import { AccountMultiPicker, CategorySelect } from "./DataPickers";
import { FiltersField } from "./FiltersField";
import { diagnoseVarRef } from "./varRefs";
import "./autoform.css";

type FieldValue = string | number | boolean;

/** A number, or any prefix of a `{{param.name}}` template reference (matched while typing). */
const NUMBER_OR_PARAM_REF = /^(-?\d*\.?\d*|\{\{?p?a?r?a?m?\.?[\w.]*\}?\}?)$/;

interface FieldSpec {
  key: string;
  title: string;
  description?: string;
  required: boolean;
  kind: "string" | "number" | "boolean" | "enum" | "unknown";
  enumValues?: (string | number)[];
  // Human captions from `x-ui.options`; without it a cron enum renders the raw expression.
  options?: PickerOption[];
  /** Explicit position from `x-ui.order`; absent means "keep declaration order". */
  order?: number;
  widget?: JsonSchemaUi["widget"];
  slider?: { min: number; max: number; step: number; unit?: string };
}

// `$defs` is threaded through because a `$ref` resolves only against the ROOT schema.
function resolveType(
  schema: JsonSchema,
  defs: Record<string, JsonSchema>,
): { type?: string; enumValues?: (string | number)[] } {
  const ref = typeof schema.$ref === "string" ? schema.$ref : null;
  if (ref) {
    const target = defs[ref.replace("#/$defs/", "")];
    if (target) return resolveType(target, defs);
  }
  if (schema.enum) return { type: schema.type, enumValues: schema.enum };
  if (schema.anyOf) {
    const real = schema.anyOf.find((s) => s.$ref ?? (s.type && s.type !== "null"));
    if (real) return resolveType(real, defs);
  }
  return { type: schema.type };
}

function toFieldSpec(
  key: string,
  schema: JsonSchema,
  required: Set<string>,
  defs: Record<string, JsonSchema>,
): FieldSpec {
  const { type, enumValues } = resolveType(schema, defs);
  const title = schema.title ?? key;
  const description = typeof schema.description === "string" ? schema.description : undefined;
  const isRequired = required.has(key);
  const ui = schema["x-ui"];
  if (enumValues || ui?.widget === "category_picker" || ui?.widget === "account_ref") {
    return {
      key,
      title,
      description,
      required: isRequired,
      kind: "enum",
      enumValues,
      options: ui?.options,
      order: ui?.order,
      widget: ui?.widget,
    };
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
    return { key, title, description, order: ui?.order, required: isRequired, kind: "number", slider };
  }
  if (type === "boolean") return { key, title, description, order: ui?.order, required: isRequired, kind: "boolean" };
  if (type === "string" || type === "array") {
    return { key, title, description, order: ui?.order, required: isRequired, kind: "string", widget: ui?.widget };
  }
  return { key, title, description, order: ui?.order, required: isRequired, kind: "unknown" };
}

interface AutoFormProps {
  schema: JsonSchema;
  values: Record<string, FieldValue>;
  onChange: (key: string, value: FieldValue) => void;
  /** Given, flags `{{vars.X}}` refs the backend won't substitute; omitted, no param hints shown. */
  varKeys?: readonly string[];
}

export function AutoForm({ schema, values, onChange, varKeys }: AutoFormProps) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>;
  const fields = Object.entries(properties)
    .map(([key, propSchema]) => toFieldSpec(key, propSchema, required, defs))
    // Pydantic emits inherited fields first, so a shared base's schedule field would open every
    // form asking "how often" before "who"; x-ui.order overrides declaration order to fix that.
    .map((field, index) => ({ field, index }))
    .sort((a, b) => (a.field.order ?? 0) - (b.field.order ?? 0) || a.index - b.index)
    .map(({ field }) => field);

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
        const options: PickerOption[] =
          field.options ??
          (field.enumValues ?? []).map((opt) => ({ value: String(opt), label: String(opt) }));
        return (
          <Field
            key={field.key}
            label={field.title}
            required={field.required}
            hint={field.description}
          >
            {field.widget === "filters" ? (
              // The only control that reads a SIBLING field: a filter set belongs to a category,
              // and `category` is the field next to it. Passing the whole form's values instead
              // would hand every control a dependency it does not have.
              <FiltersField
                value={raw}
                onChange={(v) => onChange(field.key, v)}
                category={String(values.category ?? "")}
              />
            ) : field.widget === "account_ref" ? (
              <AccountMultiPicker value={raw} onChange={(v) => onChange(field.key, v)} />
            ) : field.widget === "category_picker" ? (
              <CategorySelect value={raw} onChange={(v) => onChange(field.key, v)} />
            ) : field.kind === "enum" && field.widget === "radio" ? (
              <RadioGroup
                name={field.key}
                value={String(values[field.key] ?? "")}
                onChange={(v) => onChange(field.key, v)}
                options={options}
              />
            ) : field.kind === "enum" && field.widget === "multiselect" ? (
              <MultiSelect value={raw} onChange={(v) => onChange(field.key, v)} options={options} />
            ) : field.kind === "enum" ? (
              <OptionPicker
                value={String(values[field.key] ?? "")}
                onChange={(v) => onChange(field.key, v)}
                options={options}
                placeholder="выберите…"
              />
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
              // Kept as raw string: live Number() coercion eats a trailing "." and breaks decimal
              // entry; also must accept a partial `{{param.x}}` ref (how composite inputs wire up).
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
            {varRefProblem(values[field.key], varKeys)}
          </Field>
        );
      })}
      {varKeys && varKeys.length > 0 ? (
        <p className="autoform__var-hint">
          Доступные параметры флоу:{" "}
          {varKeys.map((k) => (
            <code key={k}>{`{{vars.${k}}}`}</code>
          ))}{" "}
          — вставляются вместо всего значения поля.
        </p>
      ) : null}
    </div>
  );
}

// Under the control, not blocking it: an embedded ref is legal and mid-edit typing is normal.
function varRefProblem(value: FieldValue | undefined, varKeys: readonly string[] | undefined) {
  if (!varKeys) return null;
  const problem = diagnoseVarRef(value, varKeys);
  if (!problem) return null;
  return <span className="autoform__var-warning">{problem.message}</span>;
}
