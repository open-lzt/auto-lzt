import type { ImportError, ImportStage } from "../api/flowClient";
import { CloseIcon } from "./icons";
import "./import-error-report.css";

interface ImportErrorReportProps {
  errors: ImportError[];
  onDismiss: () => void;
}

const STAGE_LABELS: Record<ImportStage, string> = {
  schema: "Схема",
  compile: "Компиляция",
  dry_run: "Пробный запуск",
};

const STAGE_ORDER: ImportStage[] = ["schema", "compile", "dry_run"];

/** Grouped, dismissible report of backend-rejected import errors. Node-level canvas highlighting
 * is out of scope here (the imported flow may not even be the one currently open) — this is
 * deliberately a flat, readable list the operator can act on. */
export function ImportErrorReport({ errors, onDismiss }: ImportErrorReportProps) {
  const groups = STAGE_ORDER.map((stage) => ({
    stage,
    items: errors.filter((error) => error.stage === stage),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="import-error-report" role="alert">
      <div className="import-error-report__header">
        <p className="import-error-report__title">
          Не удалось импортировать флоу — {errors.length} ошибок
        </p>
        <button
          type="button"
          className="import-error-report__dismiss"
          onClick={onDismiss}
          aria-label="Закрыть отчёт об ошибках"
        >
          <CloseIcon size={12} />
        </button>
      </div>
      {groups.map((group) => (
        <div key={group.stage} className="import-error-report__group">
          <span className="import-error-report__badge">{STAGE_LABELS[group.stage]}</span>
          <ul className="import-error-report__items">
            {group.items.map((item, index) => (
              <li key={`${group.stage}-${index}`} className="import-error-report__item">
                <span className="import-error-report__node">{item.node_id ?? "—"}</span>
                <span className="import-error-report__message">{item.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
