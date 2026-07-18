import type { ReactNode } from "react";
import "../autoform.css";

/** Chevron for the custom select — native `appearance: none` strips the OS arrow, so the
 * dropdown affordance is drawn here instead. */
function ChevronIcon() {
  return (
    <svg
      className="ctl-select__chevron"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

interface FieldProps {
  label: string;
  required?: boolean;
  children: ReactNode;
}

/** Labelled field wrapper shared by every control. */
export function Field({ label, required, children }: FieldProps) {
  return (
    <label className="autoform__field">
      <span className="autoform__label">
        {label}
        {required ? <span className="autoform__required">*</span> : null}
      </span>
      {children}
    </label>
  );
}

interface TextFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** `decimal` renders a numeric keypad on mobile without the type=number spinner/wheel bugs. */
  inputMode?: "text" | "decimal";
}

export function TextField({ value, onChange, placeholder, inputMode = "text" }: TextFieldProps) {
  return (
    <input
      type="text"
      inputMode={inputMode}
      className="autoform__control"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

interface SelectFieldProps {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}

export function SelectField({ value, onChange, children }: SelectFieldProps) {
  return (
    <div className="ctl-select">
      <select className="autoform__control" value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
      <ChevronIcon />
    </div>
  );
}

interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
}

export function Checkbox({ checked, onChange }: CheckboxProps) {
  return (
    <input
      type="checkbox"
      className="autoform__checkbox"
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

interface SliderProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** Rendered after the numeric readout, e.g. `"s"` for a delay in seconds. */
  unit?: string;
}

export function Slider({ value, onChange, min = 0, max = 100, step = 1, unit }: SliderProps) {
  return (
    <div className="ctl-slider">
      <input
        type="range"
        className="ctl-slider__range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="ctl-slider__readout">
        {value}
        {unit ? <span className="ctl-slider__unit">{unit}</span> : null}
      </span>
    </div>
  );
}

export interface PickerOption {
  value: string;
  label: string;
}

interface PickerProps {
  value: string;
  onChange: (value: string) => void;
  options: readonly PickerOption[];
  placeholder?: string;
}

/** A styled select over a caller-provided option list — the shared base for the account and
 * category pickers, which differ only in where their options come from. */
export function OptionPicker({ value, onChange, options, placeholder }: PickerProps) {
  return (
    <SelectField value={value} onChange={onChange}>
      {placeholder ? <option value="">{placeholder}</option> : null}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </SelectField>
  );
}

export function AccountPicker(props: Omit<PickerProps, "placeholder">) {
  return <OptionPicker {...props} placeholder="Select an account…" />;
}

export function CategoryPicker(props: Omit<PickerProps, "placeholder">) {
  return <OptionPicker {...props} placeholder="Select a category…" />;
}

interface TextAreaProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function TextArea({ value, onChange, placeholder }: TextAreaProps) {
  return (
    <textarea
      className="autoform__control ctl-textarea"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={4}
    />
  );
}

interface DateTimePickerProps {
  value: string;
  onChange: (value: string) => void;
}

export function DateTimePicker({ value, onChange }: DateTimePickerProps) {
  return (
    <input
      type="datetime-local"
      className="autoform__control"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

interface RadioGroupProps {
  value: string;
  onChange: (value: string) => void;
  options: readonly PickerOption[];
  name: string;
}

export function RadioGroup({ value, onChange, options, name }: RadioGroupProps) {
  return (
    <div className="ctl-radio" role="radiogroup">
      {options.map((o) => (
        <label key={o.value} className="ctl-radio__option">
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={value === o.value}
            onChange={() => onChange(o.value)}
          />
          <span>{o.label}</span>
        </label>
      ))}
    </div>
  );
}

interface MultiSelectProps {
  /** JSON-encoded array of selected values (matches the backend multiselect wire shape). */
  value: string;
  onChange: (value: string) => void;
  options: readonly PickerOption[];
}

export function MultiSelect({ value, onChange, options }: MultiSelectProps) {
  let selected: string[] = [];
  try {
    const parsed: unknown = value ? JSON.parse(value) : [];
    if (Array.isArray(parsed)) selected = parsed.map(String);
  } catch {
    selected = [];
  }
  const toggle = (v: string) => {
    const next = selected.includes(v) ? selected.filter((s) => s !== v) : [...selected, v];
    onChange(JSON.stringify(next));
  };
  return (
    <div className="ctl-multiselect">
      {options.map((o) => (
        <label key={o.value} className="ctl-multiselect__option">
          <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
          <span>{o.label}</span>
        </label>
      ))}
    </div>
  );
}
