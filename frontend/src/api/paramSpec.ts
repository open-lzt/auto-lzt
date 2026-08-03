// Mirror of app/domain/flow_engine/spec.py ParamSpec (source of truth is the backend). Kept in
// lockstep by hand, the repo's existing DTO-mirroring convention.
//
// It lives beside the other wire DTOs and not in canvas/ because FlowSpec.params is typed by it:
// a transport type defined inside the canvas would force api/ to import from canvas/, which is the
// inverted dependency this layout exists to prevent. The UI-side helpers that interpret these
// values (validateParam, isVisible) stay in canvas/paramTypes.ts.

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
