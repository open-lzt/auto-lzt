import { Block, BlockBody, BlockFooter, BlockHeader, Button, useToast } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import type { JsonSchema } from "../../../api/catalogClient";
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

  const what = withoutKeys(preset.params_schema, [SCHEDULE_KEY]);
  const when = onlyKeys(preset.params_schema, [SCHEDULE_KEY]);
  const change = (key: string, value: FormValue) =>
    setValues((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="preset-form">
      <Block className="preset-form__block">
        <BlockHeader className="preset-form__block-head">
          <h3 className="preset-form__block-title">Что делать</h3>
        </BlockHeader>
        <BlockBody className="preset-form__block-body">
          <AutoForm schema={what} values={values} onChange={change} />
        </BlockBody>
      </Block>

      <Block className="preset-form__block">
        <BlockHeader className="preset-form__block-head">
          <h3 className="preset-form__block-title">Когда</h3>
        </BlockHeader>
        <BlockBody className="preset-form__block-body">
          <AutoForm schema={when} values={values} onChange={change} />
        </BlockBody>
        <BlockFooter className="preset-form__deploy">
          <p className="preset-form__note">
            Соберёт флоу и запустит его по расписанию. Остановить можно на вкладке «Флоу».
          </p>
          <Button
            variant="primary"
            loading={deploying}
            disabled={deploying}
            onClick={() => void handleDeploy()}
          >
            Включить
          </Button>
        </BlockFooter>
      </Block>
    </div>
  );
}

const SCHEDULE_KEY = "schedule_cron";

function pickProperties(schema: JsonSchema, keep: (key: string) => boolean): JsonSchema {
  const properties = Object.fromEntries(
    Object.entries(schema.properties ?? {}).filter(([key]) => keep(key)),
  );
  return {
    ...schema,
    properties,
    required: (schema.required ?? []).filter(keep),
  };
}

function withoutKeys(schema: JsonSchema, keys: string[]): JsonSchema {
  return pickProperties(schema, (key) => !keys.includes(key));
}

function onlyKeys(schema: JsonSchema, keys: string[]): JsonSchema {
  return pickProperties(schema, (key) => keys.includes(key));
}
