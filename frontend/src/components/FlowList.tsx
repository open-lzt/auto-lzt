import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteFlow,
  exportFlow,
  fetchFlows,
  importFlow,
  renameFlow,
  type FlowSpec,
  type FlowSummary,
  type ImportError,
} from "../api/flowClient";
import { downloadFlowSpec } from "../api/downloadFlow";
import { CloseIcon, DownloadIcon, PencilIcon } from "./icons";
import { useResizablePane } from "./ResizablePane";
import { ImportErrorReport } from "./ImportErrorReport";
import { ConfirmDialog } from "./ConfirmDialog";
import "./flow-list.css";

interface FlowListProps {
  activeFlowId: string | null;
  onSelect: (id: string) => void;
  onCreateNew: () => void;
  /** Bump to force a refetch — the list stays mounted, so a new flow otherwise needs a page reload. */
  reloadToken?: number;
  /** False in the preview build: hides write controls; export stays (read-only GET + local download). */
  canAuthor?: boolean;
}

export function FlowList({
  activeFlowId,
  onSelect,
  onCreateNew,
  reloadToken = 0,
  canAuthor = true,
}: FlowListProps) {
  const [flows, setFlows] = useState<FlowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importErrors, setImportErrors] = useState<ImportError[] | null>(null);
  const [pendingDelete, setPendingDelete] = useState<FlowSummary | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);
  const { width, handle } = useResizablePane({ paneId: "flow-list", defaultWidth: 200, min: 160, max: 420 });

  const reload = useCallback(() => {
    fetchFlows()
      .then((list) => {
        setFlows(list);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "не удалось загрузить список флоу"));
  }, []);

  useEffect(() => {
    reload();
  }, [reload, reloadToken]);

  async function handleDelete(flow: FlowSummary) {
    setBusyId(flow.id);
    try {
      await deleteFlow(flow.id);
      setPendingDelete(null);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось удалить флоу");
      setPendingDelete(null);
    } finally {
      setBusyId(null);
    }
  }

  async function handleExport(flow: FlowSummary) {
    setBusyId(flow.id);
    try {
      const spec = await exportFlow(flow.id);
      downloadFlowSpec(spec, `${flow.name}.flow.json`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось экспортировать флоу");
    } finally {
      setBusyId(null);
    }
  }

  function startRename(flow: FlowSummary) {
    setRenamingId(flow.id);
    setRenameValue(flow.name);
  }

  async function commitRename(id: string) {
    const name = renameValue.trim();
    if (!name) {
      setRenamingId(null);
      return;
    }
    setBusyId(id);
    try {
      await renameFlow(id, name);
      setRenamingId(null);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось переименовать флоу");
    } finally {
      setBusyId(null);
    }
  }

  function handleImportClick() {
    importInputRef.current?.click();
  }

  async function handleImportFile(file: File) {
    let spec: FlowSpec;
    try {
      const text = await file.text();
      spec = JSON.parse(text) as FlowSpec;
    } catch {
      setError("Файл повреждён или не является корректным JSON");
      return;
    }

    setImporting(true);
    setImportErrors(null);
    try {
      const result = await importFlow(spec);
      if (result.ok) {
        setError(null);
        reload();
      } else {
        setImportErrors(result.errors);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось импортировать флоу");
    } finally {
      setImporting(false);
    }
  }

  return (
    <>
    <aside className="flow-list" style={{ width }}>
      <div className="flow-list__header">
        <h3 className="flow-list__heading">Флоу</h3>
        <div className="flow-list__header-actions">
          <input
            ref={importInputRef}
            type="file"
            accept="application/json"
            className="flow-list__import-input"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void handleImportFile(file);
            }}
          />
          {canAuthor ? (
            <>
              <button
                type="button"
                className="flow-list__import"
                disabled={importing}
                onClick={handleImportClick}
              >
                {importing ? "импорт…" : "импорт"}
              </button>
              <button type="button" className="flow-list__new" onClick={onCreateNew}>
                + новый
              </button>
            </>
          ) : null}
        </div>
      </div>

      {error ? <p className="flow-list__error">{error}</p> : null}

      {importErrors ? (
        <ImportErrorReport errors={importErrors} onDismiss={() => setImportErrors(null)} />
      ) : null}

      {!flows ? (
        <p className="flow-list__loading">загрузка…</p>
      ) : flows.length === 0 ? (
        <div className="empty-prompt">
          <p className="empty-prompt__title">Здесь пока пусто</p>
          {canAuthor ? (
            <>
              <p className="empty-prompt__hint">Соберите первый флоу из блоков в списке.</p>
              <button type="button" className="empty-prompt__action" onClick={onCreateNew}>
                Создать флоу
              </button>
            </>
          ) : (
            <p className="empty-prompt__hint">
              Установите готовый модуль через бота — команда /modules.
            </p>
          )}
        </div>
      ) : (
        <ul className="flow-list__items">
          {flows.map((flow) => (
            <li key={flow.id} className={`flow-list__item${flow.id === activeFlowId ? " flow-list__item--active" : ""}`}>
              {renamingId === flow.id ? (
                <input
                  className="flow-list__rename-input"
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => void commitRename(flow.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void commitRename(flow.id);
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                />
              ) : (
                <button type="button" className="flow-list__name" onClick={() => onSelect(flow.id)}>
                  {flow.name}
                </button>
              )}
              <span className="flow-list__actions">
                <button
                  type="button"
                  className="flow-list__action"
                  disabled={busyId === flow.id}
                  onClick={() => void handleExport(flow)}
                  aria-label={`Экспортировать ${flow.name}`}
                >
                  <DownloadIcon />
                </button>
                {canAuthor ? (
                  <>
                    <button
                      type="button"
                      className="flow-list__action"
                      disabled={busyId === flow.id}
                      onClick={() => startRename(flow)}
                      aria-label={`Переименовать ${flow.name}`}
                    >
                      <PencilIcon />
                    </button>
                    <button
                      type="button"
                      className="flow-list__action flow-list__action--danger"
                      disabled={busyId === flow.id}
                      onClick={() => setPendingDelete(flow)}
                      aria-label={`Удалить ${flow.name}`}
                    >
                      <CloseIcon />
                    </button>
                  </>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </aside>
    {handle}
    <ConfirmDialog
      open={pendingDelete !== null}
      title="Удалить флоу?"
      confirmLabel="Удалить флоу"
      busy={pendingDelete !== null && busyId === pendingDelete.id}
      onConfirm={() => {
        if (pendingDelete) void handleDelete(pendingDelete);
      }}
      onCancel={() => setPendingDelete(null)}
    >
      «{pendingDelete?.name}» и его история запусков будут удалены безвозвратно.
    </ConfirmDialog>
    </>
  );
}
