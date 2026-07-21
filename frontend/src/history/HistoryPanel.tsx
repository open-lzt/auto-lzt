import { useCallback, useEffect, useState } from "react";
import { fetchRunHistory } from "../api/flowClient";
import type { RunStatus, RunSummary } from "../api/flowClient";
import { Loader } from "../components/Loader";
import { useResizablePane } from "../components/ResizablePane";
import { RunTraceView } from "./RunTraceView";
import "./history-panel.css";

interface HistoryPanelProps {
  flowId: string;
}

const STATUS_LABEL: Record<RunStatus, string> = {
  pending: "Ожидание",
  running: "Выполняется",
  completed: "Завершён",
  failed: "Ошибка",
};

const LIVE_STATUSES: RunStatus[] = ["pending", "running"];
const POLL_INTERVAL_MS = 2000;

function formatTimestamp(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleString("ru-RU");
}

function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms} мс`;
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)} с` : `${Math.round(seconds / 60)} мин`;
}

/** Two panes: the run list on the left, the selected run's trace on the right. The trace is the
 * substance of this screen — as an accordion under a row it left most of the surface empty and
 * pushed the list around on every open. */
export function HistoryPanel({ flowId }: HistoryPanelProps) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const { width, handle } = useResizablePane({ paneId: "run-list", defaultWidth: 340, min: 260, max: 620 });

  /** `background` keeps the current list on screen while refetching — a poll must not flash the
   * loader every couple of seconds. */
  const reload = useCallback(
    (background = false) => {
      if (!background) setRuns(null);
      setError(null);
      return fetchRunHistory(flowId)
        .then((list) => {
          setRuns(list);
          setSelectedRunId((current) => current ?? list[0]?.run_id ?? null);
        })
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : "не удалось загрузить историю запусков"),
        );
    },
    [flowId],
  );

  useEffect(() => {
    setSelectedRunId(null);
    reload();
  }, [reload]);

  const selected = runs?.find((run) => run.run_id === selectedRunId) ?? null;
  const hasLiveRun = runs?.some((run) => LIVE_STATUSES.includes(run.status)) ?? false;

  // A run finishes seconds after it is published; without this the row sits on "Выполняется" and
  // the trace pane spins forever, because nothing ever asks the server again.
  useEffect(() => {
    if (!hasLiveRun) return undefined;
    const timer = window.setInterval(() => void reload(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [hasLiveRun, reload]);

  return (
    <section className="history">
      <div className="history__list-pane" style={{ width }}>
        <div className="history__list-header">
          <h3 className="history__heading">Запуски</h3>
          <button type="button" className="history__refresh" onClick={() => void reload()}>
            обновить
          </button>
        </div>

        {error ? <p className="history__error">{error}</p> : null}

        {!runs && !error ? (
          <div className="history__loading">
            <Loader />
          </div>
        ) : runs && runs.length === 0 ? (
          <div className="empty-prompt">
            <p className="empty-prompt__title">Запусков ещё не было</p>
            <p className="empty-prompt__hint">
              Опубликуйте флоу — первый запуск стартует сразу и появится здесь.
            </p>
          </div>
        ) : runs ? (
          <ul className="history__items">
            {runs.map((run) => (
              <li key={run.run_id}>
                <button
                  type="button"
                  className={`history__row${run.run_id === selectedRunId ? " history__row--selected" : ""}`}
                  onClick={() => setSelectedRunId(run.run_id)}
                  aria-current={run.run_id === selectedRunId}
                >
                  <span className={`history__status history__status--${run.status}`}>
                    {STATUS_LABEL[run.status]}
                  </span>
                  <span className="history__started">{formatTimestamp(run.started_at)}</span>
                  <span className="history__duration">{formatDuration(run.duration_ms)}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {handle}
      <div className="history__trace-pane">
        {selected ? (
          <>
            <div className="history__trace-header">
              <h3 className="history__heading">Трассировка</h3>
              <span className="history__trace-meta">
                {formatTimestamp(selected.started_at)} · {formatDuration(selected.duration_ms)}
              </span>
            </div>
            {/* The reason sits ABOVE the steps, not inside them: the steps say what ran, this says
                why the run stopped. A failed run whose cause is only a red badge is the exact
                complaint this panel exists to answer. `failed_node_id` is the graph's own node id
                — the same string the canvas labels the block with — so it points at a place you
                can actually go and look. */}
            {selected.error ? (
              <div className="history__failure" role="alert">
                <span className="history__failure-label">
                  Упало{selected.failed_node_id ? ` на шаге «${selected.failed_node_id}»` : ""}
                </span>
                <code className="history__failure-cause">{selected.error}</code>
              </div>
            ) : null}
            <RunTraceView
              key={`${selected.run_id}:${selected.status}`}
              runId={selected.run_id}
              live={LIVE_STATUSES.includes(selected.status)}
            />
          </>
        ) : (
          <div className="empty-prompt">
            <p className="empty-prompt__title">Запуск не выбран</p>
            <p className="empty-prompt__hint">Выберите запуск в списке, чтобы увидеть его шаги.</p>
          </div>
        )}
      </div>
    </section>
  );
}
