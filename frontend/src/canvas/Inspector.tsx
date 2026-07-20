import type { CatalogNode, TriggerKind } from "../api/flowClient";
import { AutoForm } from "../components/form/AutoForm";
import type { CanvasNodeData, TriggerConfig } from "./canvasTypes";
import { displayLabel } from "./labels";
import { Field, SelectField, TextField } from "../components/form/controls";
import { useResizablePane } from "../components/ResizablePane";
import "./inspector.css";

const CURATED_EVENT_TYPES = [
  "new_message",
  "item_sold",
  "new_lot",
  "price_dropped",
  "lot_disappeared",
  "balance_refilled",
];

interface InspectorProps {
  node: { id: string; data: CanvasNodeData } | null;
  catalogEntry: CatalogNode | undefined;
  onChangeValue: (key: string, value: string | number | boolean) => void;
  onChangeTrigger: (patch: Partial<TriggerConfig>) => void;
  onRenameLabel: (label: string) => void;
  onDeleteNode: () => void;
  onDuplicateNode: () => void;
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h16M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
    </svg>
  );
}

/** Side panel bound to the currently selected canvas node — renders AutoForm for action/logic
 * blocks, or the trigger's kind/cron/event config for a trigger block. */
export function Inspector({
  node,
  catalogEntry,
  onChangeValue,
  onChangeTrigger,
  onRenameLabel,
  onDeleteNode,
  onDuplicateNode,
}: InspectorProps) {
  const { width, handle } = useResizablePane({
    paneId: "inspector",
    defaultWidth: 260,
    min: 220,
    max: 560,
    edge: "left",
  });

  if (!node) {
    return (
      <>
        {handle}
        <aside className="inspector inspector--empty" style={{ width }}>
          <p>выберите блок на холсте, чтобы настроить его</p>
        </aside>
      </>
    );
  }

  return (
    <>
    {handle}
    <aside className="inspector" style={{ width }}>
      <div className="inspector__header">
        <input
          className="inspector__title-input"
          value={node.data.label || displayLabel(node.data.catalogKey)}
          placeholder={displayLabel(node.data.catalogKey)}
          onChange={(e) => onRenameLabel(e.target.value)}
          aria-label="Название блока"
        />
        <div className="inspector__actions">
          <button type="button" className="inspector__icon-btn" onClick={onDuplicateNode} title="Дублировать блок">
            <CopyIcon />
          </button>
          <button
            type="button"
            className="inspector__icon-btn inspector__icon-btn--danger"
            onClick={onDeleteNode}
            title="Удалить блок"
          >
            <TrashIcon />
          </button>
        </div>
      </div>

      {node.data.category === "trigger" && node.data.triggerConfig ? (
        <div className="autoform">
          <Field label="Тип">
            <SelectField
              value={node.data.triggerConfig.kind}
              onChange={(v) => onChangeTrigger({ kind: v as TriggerKind })}
            >
              <option value="manual">вручную</option>
              <option value="schedule">по расписанию</option>
              <option value="event">по событию</option>
            </SelectField>
          </Field>
          {node.data.triggerConfig.kind === "schedule" ? (
            <Field label="Cron-выражение">
              <TextField
                placeholder="*/30 * * * *"
                value={node.data.triggerConfig.schedule_cron}
                onChange={(v) => onChangeTrigger({ schedule_cron: v })}
              />
            </Field>
          ) : null}
          {node.data.triggerConfig.kind === "event" ? (
            <Field label="Событие">
              <SelectField
                value={node.data.triggerConfig.event_type}
                onChange={(v) => onChangeTrigger({ event_type: v })}
              >
                <option value="" disabled>
                  выберите…
                </option>
                {CURATED_EVENT_TYPES.map((et) => (
                  <option key={et} value={et}>
                    {et}
                  </option>
                ))}
              </SelectField>
            </Field>
          ) : null}
        </div>
      ) : catalogEntry ? (
        <AutoForm schema={catalogEntry.input_schema} values={node.data.values} onChange={onChangeValue} />
      ) : (
        <p className="inspector__loading">загрузка параметров…</p>
      )}
    </aside>
    </>
  );
}
