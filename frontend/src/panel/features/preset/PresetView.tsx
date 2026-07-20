import { Alert, Button, Card, Icon, useToast } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { AutoForm } from "../../../components/form/AutoForm";
import {
  coerceParams,
  defaultValues,
  deployPreset,
  fetchPresets,
  type PresetSummary,
} from "./presetClient";
import "../preset-form.css";

type FormValue = string | number | boolean;

export interface PresetViewProps {
  /** Which preset to render — the panel tab key, which matches the preset key. */
  presetKey: string;
  onDeployed?: () => void;
}

/** The one screen behind every preset tab.
 *
 * There is no per-preset component on purpose. A preset states its own fields server-side
 * (`domain/panel/preset_registry.py`) and this renders whatever it states, so adding a preset is
 * a backend change and this file never grows. The previous shape — one hand-written React screen
 * per preset — meant the interval list and the market's category enum were re-typed in
 * TypeScript, where they immediately began to drift.
 */
export function PresetView({ presetKey, onDeployed }: PresetViewProps) {
  const [preset, setPreset] = useState<PresetSummary | null>(null);
  const [values, setValues] = useState<Record<string, FormValue>>({});
  const [error, setError] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    fetchPresets()
      .then((all) => {
        if (cancelled) return;
        const found = all.find((p) => p.key === presetKey) ?? null;
        setPreset(found);
        if (found) setValues(defaultValues(found.params_schema));
        else setError(`пресет «${presetKey}» не найден на сервере`);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "не удалось загрузить пресет"),
      );
    return () => {
      cancelled = true;
    };
  }, [presetKey]);

  async function handleDeploy(): Promise<void> {
    if (!preset) return;
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

  if (error) {
    return (
      <div className="panel-view">
        <Alert tone="danger" title="Пресет не загрузился">
          {error}
        </Alert>
      </div>
    );
  }

  if (!preset) {
    return (
      <div className="panel-view">
        <p className="preset-form__note">загрузка…</p>
      </div>
    );
  }

  return (
    <div className="panel-view">
      <div className="panel-view__head">
        <h2 className="panel-view__title">
          <Icon name={preset.icon} size={20} />
          {preset.title}
        </h2>
      </div>

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
    </div>
  );
}
