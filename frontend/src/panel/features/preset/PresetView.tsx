import { Button, Card, useToast } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { AutoForm } from "../../../components/form/AutoForm";
import { coerceParams, defaultValues, deployPreset, type PresetSummary } from "./presetClient";
import "../preset-form.css";

type FormValue = string | number | boolean;

export interface PresetFormProps {
  preset: PresetSummary;
  onDeployed?: () => void;
}

/** The form for ONE preset — presentational, handed the preset it renders.
 *
 * There is no per-preset component on purpose. A preset states its own fields server-side
 * (`domain/panel/preset_registry.py`) and this renders whatever it states, so adding a preset is
 * a backend change and this file never grows. The shape before — one hand-written React screen
 * per preset — meant the interval list and the market's category enum were re-typed in
 * TypeScript, where they immediately began to drift.
 */
export function PresetForm({ preset, onDeployed }: PresetFormProps) {
  const [values, setValues] = useState<Record<string, FormValue>>(() =>
    defaultValues(preset.params_schema),
  );
  const [deploying, setDeploying] = useState(false);
  const toast = useToast();

  // Switching preset resets the form: the previous preset's values are keyed by ITS field names,
  // and carrying them over would submit fields the new preset never declared.
  useEffect(() => {
    setValues(defaultValues(preset.params_schema));
  }, [preset]);

  async function handleDeploy(): Promise<void> {
    setDeploying(true);
    try {
      await deployPreset(preset.key, coerceParams(preset.params_schema, values));
      toast.show(`${preset.title} — включено`);
      onDeployed?.();
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось включить", { tone: "danger" });
    } finally {
      setDeploying(false);
    }
  }

  return (
    <div className="preset-form">
      <Card className="preset-form__section">
        <AutoForm
          schema={preset.params_schema}
          values={values}
          onChange={(key, value) => setValues((prev) => ({ ...prev, [key]: value }))}
        />
      </Card>

      <div className="preset-form__deploy">
        <Button
          variant="primary"
          loading={deploying}
          disabled={deploying}
          onClick={() => void handleDeploy()}
        >
          Включить
        </Button>
      </div>
    </div>
  );
}
