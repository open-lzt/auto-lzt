import type { JsonSchema, JsonSchemaUi } from "../../api/flowClient";
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
  // Human captions for `enumValues`, from `x-ui.options`. JSON Schema has nowhere to put them,
  // and without it a cron-valued enum renders the raw expression instead of «Каждые 30 минут».
  options?: PickerOption[];
  /** Explicit position from `x-ui.order`; absent means "keep declaration order". */
  order?: number;
  // x-ui.widget picks the control within a kind: radio/multiselect for enum, textarea/datetime
  // for string. "slider" is number-only, so it carries its own config below instead of here.
  widget?: JsonSchemaUi["widget"];
  slider?: { min: number; max: number; step: number; unit?: string };
}

/** Unwrap a property schema down to the type that decides which control to render.
 *
 * Three shapes arrive from Pydantic and all three used to fall through to "unknown":
 *   - `anyOf: [{type: X}, {type: "null"}]` for `X | None`;
 *   - `$ref: "#/$defs/Name"` for ANY enum-typed field — which is every picker in the app;
 *   - a plain inline `enum`.
 * `$defs` is threaded through because a `$ref` can only be resolved against the ROOT schema.
 */
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
}

/** Renders one input per JSON-Schema property.
 *
 * Drives both surfaces that describe their fields as a schema: a node's `input_schema` from GET
 * /catalog, and a preset's parameter model from GET /panel/presets/list. Supports
 * string/number/boolean/enum, with `x-ui.widget` upgrading string to textarea/datetime, enum to
 * radio/multiselect/account/category pickers, and number to slider; anything more exotic falls
 * back to a plain text field rather than silently dropping the parameter. */
export function AutoForm({ schema, values, onChange }: AutoFormProps) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>;
  const fields = Object.entries(properties)
    .map(([key, propSchema]) => toFieldSpec(key, propSchema, required, defs))
    // Declaration order is the default, but a field may claim a position with `x-ui.order`.
    // Needed because Pydantic emits INHERITED fields first: a schedule declared on a shared base
    // would open every form with «Как часто», asking when before who. Stable sort keeps
    // everything without an order in its declared sequence.
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
        // `x-ui.options` wins over the raw enum values: the schema knows the allowed set, the
        // ui hint knows what to call each one.
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
            {field.widget === "account_ref" ? (
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
