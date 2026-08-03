import {
  Checkbox as KitCheckbox,
  DateTimePicker as KitDateTimePicker,
  Input,
  Radio,
  Select,
  Slider as KitSlider,
  Textarea,
  type SelectOption,
} from "@open-lzt/ui";
import type { ReactNode } from "react";
import "./autoform.css";

export interface PickerOption {
  value: string;
  label: string;
}

interface FieldProps {
  label: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}

export function Field({ label, required, hint, children }: FieldProps) {
  return (
    <label className="autoform__field">
      <span className="autoform__label">
        {label}
        {required ? <span className="autoform__required">*</span> : null}
      </span>
      {children}
      {hint ? <span className="autoform__hint">{hint}</span> : null}
    </label>
  );
}

interface TextFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** `decimal` gives a numeric keypad without the type=number spinner and wheel bugs. */
  inputMode?: "text" | "decimal";
}

export function TextField({ value, onChange, placeholder, inputMode = "text" }: TextFieldProps) {
  return (
    <Input
      inputMode={inputMode}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

interface TextAreaProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function TextArea({ value, onChange, placeholder }: TextAreaProps) {
  return (
    <Textarea
      rows={4}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
}

export function Checkbox({ checked, onChange }: CheckboxProps) {
  return <KitCheckbox checked={checked} onChange={(e) => onChange(e.target.checked)} />;
}

interface SliderProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}

export function Slider({ value, onChange, min = 0, max = 100, step = 1, unit }: SliderProps) {
  return (
    <KitSlider value={value} onChange={onChange} min={min} max={max} step={step} unit={unit} />
  );
}

interface DateTimePickerProps {
  value: string;
  onChange: (value: string) => void;
}

export function DateTimePicker({ value, onChange }: DateTimePickerProps) {
  return <KitDateTimePicker value={value} onChange={onChange} />;
}

interface PickerProps {
  value: string;
  onChange: (value: string) => void;
  options: readonly PickerOption[];
  placeholder?: string;
}

export function OptionPicker({ value, onChange, options, placeholder }: PickerProps) {
  return (
    <Select
      options={options as SelectOption[]}
      value={value}
      onChange={onChange}
      placeholder={placeholder ?? "выберите…"}
    />
  );
}

export function AccountPicker(props: Omit<PickerProps, "placeholder">) {
  return <OptionPicker {...props} placeholder="выберите аккаунт…" />;
}

export function CategoryPicker(props: Omit<PickerProps, "placeholder">) {
  return <OptionPicker {...props} placeholder="выберите категорию…" />;
}

interface SelectFieldProps {
  value: string;
  onChange: (value: string) => void;
  options: readonly PickerOption[];
  placeholder?: string;
}

export function SelectField(props: SelectFieldProps) {
  return <OptionPicker {...props} />;
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
        <Radio
          key={o.value}
          label={o.label}
          name={name}
          value={o.value}
          checked={value === o.value}
          onChange={() => onChange(o.value)}
        />
      ))}
    </div>
  );
}

interface MultiSelectProps {
  /** JSON-encoded array of selected values — the backend multiselect wire shape. */
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
        <KitCheckbox
          key={o.value}
          label={o.label}
          checked={selected.includes(o.value)}
          onChange={() => toggle(o.value)}
        />
      ))}
    </div>
  );
}
