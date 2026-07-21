import { Alert, Icon } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { PresetForm } from "./PresetView";
import { fetchPresets, type PresetSummary } from "./presetClient";
import "../preset-form.css";

export interface AutomationViewProps {
  onDeployed?: () => void;
}

/** One tab for every preset, not one tab each.
 *
 * Each preset used to own a top-level tab, which meant adding a preset still required editing the
 * tab list — the last piece of per-preset hardcoding left after the forms themselves became
 * server-declared. They are all the same thing (a form that authors a flow), so they are one
 * destination with a switcher, and the switcher is built from whatever `/panel/presets/list`
 * returns. Adding a preset now touches no frontend file at all.
 */
export function AutomationView({ onDeployed }: AutomationViewProps) {
  const [presets, setPresets] = useState<PresetSummary[] | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPresets()
      .then((all) => {
        if (cancelled) return;
        setPresets(all);
        setActiveKey((current) => current ?? all[0]?.key ?? null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "не удалось загрузить пресеты"),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="panel-view">
        <Alert tone="danger" title="Автоматизация не загрузилась">
          {error}
        </Alert>
      </div>
    );
  }

  if (presets === null) {
    return (
      <div className="panel-view">
        <p className="preset-form__note">загрузка…</p>
      </div>
    );
  }

  const active = presets.find((p) => p.key === activeKey) ?? null;

  return (
    <div className="panel-view">
      <div className="panel-view__head">
        <h2 className="panel-view__title">Автоматизация</h2>
        <p className="panel-view__subtitle">
          Каждая настройка собирает обычный флоу и вешает на него расписание.
          <br />
          Его можно открыть и доработать на вкладке «Флоу».
        </p>
      </div>

      <div className="preset-switch" role="tablist" aria-label="Пресеты">
        {presets.map((preset) => (
          <button
            key={preset.key}
            type="button"
            role="tab"
            aria-selected={preset.key === activeKey}
            className={
              preset.key === activeKey
                ? "preset-switch__item preset-switch__item--active"
                : "preset-switch__item"
            }
            onClick={() => setActiveKey(preset.key)}
          >
            <Icon name={preset.icon} size={16} />
            {preset.title}
          </button>
        ))}
      </div>

      {active ? <PresetForm preset={active} onDeployed={onDeployed} /> : null}
    </div>
  );
}
