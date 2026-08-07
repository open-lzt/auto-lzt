import { useEffect, useMemo, useState } from "react";
import {
  fetchCategoryFilters,
  type CategoryFiltersDTO,
  type JsonSchema,
} from "../../api/catalogClient";
import { Checkbox, Field, OptionPicker, TextField, type PickerOption } from "./controls";
import "./filters-field.css";

type FilterValue = string | number | boolean;

interface FiltersFieldProps {
  /** JSON object, as a string — the wire shape of the node's single `filters` port. */
  value: string;
  onChange: (value: string) => void;
  /** Slug of the sibling `category` field. The filter set has no meaning without it. */
  category: string;
}

/**
 * The search filter surface: a curated handful, then everything else behind a disclosure.
 *
 * One port, not one per filter. Steam declares 123 filters and VPN 22, and a node whose port list
 * changed shape on every category switch would have to be recompiled each time — so the value
 * travels as a JSON object and this component is the only thing that knows its shape.
 *
 * Renders from the server's JSON Schema rather than a hand-written field list: the schema is
 * reflected out of pylzt's own signatures, so a filter added upstream appears here with no edit.
 */
export function FiltersField({ value, onChange, category }: FiltersFieldProps) {
  const [schema, setSchema] = useState<CategoryFiltersDTO | null>(null);
  const [failed, setFailed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [rawMode, setRawMode] = useState(false);

  useEffect(() => {
    if (!category) return;
    let live = true;
    setSchema(null);
    setFailed(false);
    fetchCategoryFilters(category)
      .then((dto) => live && setSchema(dto))
      .catch(() => live && setFailed(true));
    // Guards against a slow response for a category the operator already switched away from
    // overwriting the schema of the one now selected.
    return () => {
      live = false;
    };
  }, [category]);

  const parsed = useMemo(() => parseFilters(value), [value]);

  function set(key: string, next: FilterValue | undefined): void {
    const draft = { ...parsed.values };
    // Clearing a filter DELETES it rather than storing "": an empty string is a value the backend
    // would try to coerce, and for a number field it would reject the whole run.
    if (next === undefined || next === "") delete draft[key];
    else draft[key] = next;
    onChange(Object.keys(draft).length === 0 ? "{}" : JSON.stringify(draft));
  }

  if (!category) return <p className="filters__note">сначала выберите категорию</p>;
  if (failed) return <p className="filters__note">не удалось загрузить фильтры категории</p>;
  if (schema === null) return <p className="filters__note">загрузка фильтров…</p>;

  const properties = schema.json_schema.properties ?? {};
  const curated = schema.curated.filter((name) => name in properties);
  const rest = Object.keys(properties).filter((name) => !curated.includes(name));
  const activeInRest = rest.filter((name) => name in parsed.values).length;

  // A filter the operator set that this category does not declare — left after switching category.
  // Named rather than silently dropped: it is still in the value the run would send, and the
  // backend rejects the whole search on it.
  const stale = Object.keys(parsed.values).filter((name) => !(name in properties));

  return (
    <div className="filters">
      {parsed.broken ? (
        <p className="filters__error">
          значение не разбирается как JSON — правьте текстом, иначе изменения потеряются
        </p>
      ) : null}
      {stale.length > 0 ? (
        <p className="filters__error">
          не из этой категории: {stale.join(", ")} — поиск откажет, пока их не убрать
        </p>
      ) : null}

      {rawMode || parsed.broken ? (
        <textarea
          className="filters__raw"
          value={value}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <>
          <div className="filters__grid">
            {curated.map((name) => (
              <FilterControl
                key={name}
                name={name}
                schema={properties[name]}
                value={parsed.values[name]}
                onChange={(v) => set(name, v)}
              />
            ))}
          </div>

          {rest.length > 0 ? (
            <>
              <button
                type="button"
                className="filters__disclosure"
                aria-expanded={showAll}
                onClick={() => setShowAll((s) => !s)}
              >
                {showAll ? "Свернуть" : `Все фильтры (${rest.length})`}
                {activeInRest > 0 ? <span className="filters__badge">{activeInRest}</span> : null}
              </button>
              {showAll ? (
                <div className="filters__grid">
                  {rest.map((name) => (
                    <FilterControl
                      key={name}
                      name={name}
                      schema={properties[name]}
                      value={parsed.values[name]}
                      onChange={(v) => set(name, v)}
                    />
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </>
      )}

      <button type="button" className="filters__mode" onClick={() => setRawMode((r) => !r)}>
        {rawMode ? "Показать поля" : "Править как JSON"}
      </button>
    </div>
  );
}

interface FilterControlProps {
  name: string;
  schema: JsonSchema;
  value: FilterValue | undefined;
  onChange: (value: FilterValue | undefined) => void;
}

/**
 * One filter, typed off its JSON Schema.
 *
 * Arrays fall back to a comma-separated text field rather than a multi-select: the list kinds carry
 * up to hundreds of ids (`tag_id`, `game`) and a picker over those is worse than typing them.
 */
function FilterControl({ name, schema, value, onChange }: FilterControlProps) {
  const title = schema.title ?? name;

  if (schema.type === "boolean") {
    return (
      <label className="filters__row">
        <Checkbox checked={value === true} onChange={(v) => onChange(v ? true : undefined)} />
        <span className="filters__name">{title}</span>
      </label>
    );
  }

  if (Array.isArray(schema.enum)) {
    const options: PickerOption[] = schema.enum.map((v) => ({
      value: String(v),
      label: String(v),
    }));
    return (
      <Field label={title}>
        <OptionPicker
          value={value === undefined ? "" : String(value)}
          onChange={(v) => onChange(v === "" ? undefined : v)}
          options={options}
          placeholder="не важно"
        />
      </Field>
    );
  }

  if (schema.type === "integer" || schema.type === "number") {
    return (
      <Field label={title}>
        <TextField
          inputMode="decimal"
          value={value === undefined ? "" : String(value)}
          onChange={(v) => onChange(v === "" ? undefined : toNumberOrText(v, schema.type))}
        />
      </Field>
    );
  }

  if (schema.type === "array") {
    return (
      <Field label={title} hint="через запятую">
        <TextField
          value={value === undefined ? "" : String(value)}
          onChange={(v) => onChange(v === "" ? undefined : v)}
        />
      </Field>
    );
  }

  return (
    <Field label={title}>
      <TextField
        value={value === undefined ? "" : String(value)}
        onChange={(v) => onChange(v === "" ? undefined : v)}
      />
    </Field>
  );
}

/**
 * Kept as text while it is not yet a number, so typing "1." or "-" is possible.
 *
 * The backend coerces a numeric string, so a half-typed value costs nothing; what it refuses is a
 * bool in a number field, which cannot be produced here.
 */
function toNumberOrText(raw: string, type?: string): FilterValue {
  const parsed = type === "integer" ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
  if (Number.isNaN(parsed) || String(parsed) !== raw.trim()) return raw;
  return parsed;
}

function parseFilters(value: string): { values: Record<string, FilterValue>; broken: boolean } {
  if (!value.trim()) return { values: {}, broken: false };
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { values: {}, broken: true };
    }
    return { values: parsed as Record<string, FilterValue>, broken: false };
  } catch {
    return { values: {}, broken: true };
  }
}
