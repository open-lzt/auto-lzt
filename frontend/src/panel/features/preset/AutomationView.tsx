import { Alert, Icon, Segmented, Skeleton } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { PresetForm } from "./PresetView";
import { fetchPresets, type PresetSummary } from "./presetClient";
import "../preset-form.css";

export interface AutomationViewProps {
  onDeployed?: () => void;
}

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
      <div className="panel-view" aria-busy="true">
        <div className="preset-switch">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="preset-switch__item--skeleton" />
          ))}
        </div>
        <Skeleton className="preset-form__skeleton" />
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

      <Segmented
        className="preset-switch"
        aria-label="Пресеты"
        value={activeKey ?? undefined}
        onChange={setActiveKey}
        items={presets.map((preset) => ({
          value: preset.key,
          label: (
            <span className="preset-switch__label">
              <Icon name={preset.icon} size={16} />
              {preset.title}
            </span>
          ),
        }))}
      />

      {active ? <PresetForm preset={active} onDeployed={onDeployed} /> : null}
    </div>
  );
}
