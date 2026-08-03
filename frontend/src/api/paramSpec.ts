// Mirrors app/domain/flow_engine/spec.py ParamSpec — keep in lockstep, backend is source of truth.
// Lives here (not canvas/) because FlowSpec.params is typed by it and api/ must not import from
// canvas/. UI-side interpretation (validateParam, isVisible) stays in canvas/paramTypes.ts.

export type ParamControl =
  | "text"
  | "number"
  | "slider"
  | "toggle"
  | "select"
  | "account_picker"
  | "category_picker"
  | "delay"
  | "multiselect"
  | "datetime"
  | "radio"
  | "textarea";

export interface ParamOption {
  value: string | number;
  label: string;
}

export type ParamValue = string | number | boolean | null;

export interface ParamVisibility {
  field: string;
  equals: string | number | boolean;
}

export interface ParamSpec {
  key: string;
  label: string;
  control: ParamControl;
  default?: ParamValue;
  required: boolean;
  description?: string | null;
  minimum?: number | null;
  maximum?: number | null;
  step?: number | null;
  options?: ParamOption[] | null;
  group?: string | null;
  visible_if?: ParamVisibility | null;
}
