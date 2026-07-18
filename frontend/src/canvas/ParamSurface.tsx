// The flat "settings menu" for a flow: renders its declared params as a form ("Задержка: X",
// "Купить аккаунтов: N") so values live in one place instead of scattered across node inspectors.

import { useEffect, useState } from "react";

import {
  AccountPicker,
  CategoryPicker,
  Checkbox,
  DateTimePicker,
  Field,
  MultiSelect,
  OptionPicker,
  RadioGroup,
  Slider,
  TextArea,
  TextField,
} from "./ui/controls";
import { MARKET_CATEGORIES, isVisible, validateParam } from "./paramTypes";
import type { ParamSpec, ParamValue } from "./paramTypes";
import { fetchCategories } from "../api/flowClient";
import type { PickerOption } from "./ui/controls";
import "./autoform.css";

/** Live market categories with the static snapshot as an offline fallback (closes the wave-01
 * hardcode: the picker prefers GET /catalog/categories, degrades to the bundled list on failure). */
function useCategories(): readonly PickerOption[] {
  const [categories, setCategories] = useState<readonly PickerOption[]>(MARKET_CATEGORIES);
  useEffect(() => {
    let alive = true;
    fetchCategories()
      .then((cats) => {
        if (alive && cats.length > 0) setCategories(cats.map((c) => ({ value: c.slug, label: c.label })));
      })
      .catch(() => {
        /* keep the static fallback */
      });
    return () => {
      alive = false;
    };
  }, []);
  return categories;
}

export interface AccountRef {
  id: string;
  label: string;
}

interface ParamSurfaceProps {
  params: ParamSpec[];
  values: Record<string, ParamValue>;
  onChange: (key: string, value: ParamValue) => void;
  /** Accounts offered by an account_picker param — supplied by the parent (fetched once). */
  accounts?: AccountRef[];
}

function controlFor(
  spec: ParamSpec,
  value: ParamValue,
  set: (v: ParamValue) => void,
  accounts: AccountRef[],
  categories: readonly PickerOption[],
) {
  switch (spec.control) {
    case "toggle":
      return <Checkbox checked={value === true} onChange={set} />;
    case "slider":
    case "delay":
      return (
        <Slider
          value={typeof value === "number" ? value : Number(value ?? spec.minimum ?? 0)}
          onChange={set}
          min={spec.minimum ?? 0}
          max={spec.maximum ?? 100}
          step={spec.step ?? 1}
          unit={spec.control === "delay" ? "s" : undefined}
        />
      );
    case "select":
      return (
        <OptionPicker
          value={value == null ? "" : String(value)}
          onChange={set}
          options={(spec.options ?? []).map((o) => ({ value: String(o.value), label: o.label }))}
        />
      );
    case "category_picker":
      return (
        <CategoryPicker value={value == null ? "" : String(value)} onChange={set} options={categories} />
      );
    case "account_picker":
      return (
        <AccountPicker
          value={value == null ? "" : String(value)}
          onChange={set}
          options={accounts.map((a) => ({ value: a.id, label: a.label }))}
        />
      );
    case "radio":
      return (
        <RadioGroup
          name={spec.key}
          value={value == null ? "" : String(value)}
          onChange={set}
          options={(spec.options ?? []).map((o) => ({ value: String(o.value), label: o.label }))}
        />
      );
    case "multiselect":
      return (
        <MultiSelect
          value={value == null ? "" : String(value)}
          onChange={set}
          options={(spec.options ?? []).map((o) => ({ value: String(o.value), label: o.label }))}
        />
      );
    case "datetime":
      return <DateTimePicker value={value == null ? "" : String(value)} onChange={set} />;
    case "textarea":
      return <TextArea value={value == null ? "" : String(value)} onChange={set} />;
    case "number":
      return (
        <TextField
          value={value == null ? "" : String(value)}
          onChange={(v) => set(v === "" ? null : Number(v))}
          inputMode="decimal"
        />
      );
    default:
      return (
        <TextField value={value == null ? "" : String(value)} onChange={set} />
      );
  }
}

function ParamRow({
  spec,
  values,
  onChange,
  accounts,
  categories,
}: {
  spec: ParamSpec;
  values: Record<string, ParamValue>;
  onChange: (key: string, value: ParamValue) => void;
  accounts: AccountRef[];
  categories: readonly PickerOption[];
}) {
  const value = values[spec.key] ?? spec.default ?? null;
  const error = validateParam(spec, value);
  return (
    <div className="param-surface__row">
      <Field label={spec.label} required={spec.required}>
        {controlFor(spec, value, (v) => onChange(spec.key, v), accounts, categories)}
      </Field>
      {spec.description ? <span className="autoform__description">{spec.description}</span> : null}
      {error ? <span className="autoform__error">{error}</span> : null}
    </div>
  );
}

export function ParamSurface({ params, values, onChange, accounts = [] }: ParamSurfaceProps) {
  const categories = useCategories();
  if (params.length === 0) {
    return <p className="autoform__empty">This flow has no configurable parameters.</p>;
  }
  const visible = params.filter((spec) => isVisible(spec, values));
  // Preserve declaration order of groups; params without a group render under the empty-key group.
  const groups: string[] = [];
  for (const spec of visible) {
    const g = spec.group ?? "";
    if (!groups.includes(g)) groups.push(g);
  }
  return (
    <div className="param-surface">
      {groups.map((group) => (
        <div className="param-surface__group" key={group || "__ungrouped"}>
          {group ? <h4 className="param-surface__group-title">{group}</h4> : null}
          {visible
            .filter((spec) => (spec.group ?? "") === group)
            .map((spec) => (
              <ParamRow
                key={spec.key}
                spec={spec}
                values={values}
                onChange={onChange}
                accounts={accounts}
                categories={categories}
              />
            ))}
        </div>
      ))}
    </div>
  );
}
