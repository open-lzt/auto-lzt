import { useEffect, useRef, useState } from "react";
import { fetchRunTrace, streamRun } from "../api/flowClient";
import type { RunTraceStep, StepCompletedEvent } from "../api/flowClient";
import { displayLabel } from "../canvas/labels";
import { Loader } from "../components/Loader";
import "./run-trace-view.css";

interface RunTraceViewProps {
  runId: string;
  /** When true, subscribes to the run's SSE feed and appends steps as they complete, on top of
   * whatever the static trace already had at mount. */
  live?: boolean;
}

interface LiveStep {
  nodeId: string;
  nodeType: string;
  durationMs: number;
  iterationKey: string | null;
}

type TraceEntry = { kind: "static"; branchId: string | null; step: RunTraceStep } | { kind: "live"; entry: LiveStep };

interface EntryGroup {
  branchId: string | null;
  entries: TraceEntry[];
}

function groupByBranch(entries: TraceEntry[]): EntryGroup[] {
  const groups: EntryGroup[] = [];
  for (const entry of entries) {
    const branchId = entry.kind === "static" ? entry.branchId : null;
    const last = groups[groups.length - 1];
    if (last && last.branchId === branchId) {
      last.entries.push(entry);
    } else {
      groups.push({ branchId, entries: [entry] });
    }
  }
  return groups;
}

/** Renders the ordered step trace of one run. Steps sharing a `branch_id` (fork/join
 * concurrency, wave-06) are visually grouped together via a coloured left border so parallel
 * branches read as distinct lanes rather than one flat sequence.
 *
 * In `live` mode it additionally subscribes to the run's SSE feed and appends each
 * `step_completed` event as it arrives. Dedup heuristic: a per-`node_id` counter is seeded from
 * the static trace fetched at mount (how many times that node had already completed); the first
 * N live events for a given node_id are treated as the same occurrences already rendered by the
 * static fetch and dropped, everything past that renders as a new live row. This is an
 * approximation (it can't match `iteration_key` — `RunTraceStep` doesn't carry one on the wire)
 * but covers the only race that matters: a step finishing in the gap between the static fetch
 * and the SSE subscription opening. */
export function RunTraceView({ runId, live = false }: RunTraceViewProps) {
  const [steps, setSteps] = useState<RunTraceStep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([]);
  const seenCounts = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    setSteps(null);
    setError(null);
    setLiveSteps([]);
    seenCounts.current = new Map();
    fetchRunTrace(runId)
      .then((trace) => {
        setSteps(trace.steps);
        const counts = new Map<string, number>();
        for (const step of trace.steps) {
          counts.set(step.node_id, (counts.get(step.node_id) ?? 0) + 1);
        }
        seenCounts.current = counts;
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "не удалось загрузить трассировку запуска"));
  }, [runId]);

  // streamRun now mints a short-lived token before opening the connection, so subscribing is
  // asynchronous: the effect can be torn down while that request is still in flight. Without the
  // cancelled flag the stream would open after unmount and never be closed.
  useEffect(() => {
    if (!live) return undefined;

    let cancelled = false;
    let unsubscribe: (() => void) | null = null;

    streamRun(runId, (event) => {
      if (event.type !== "step_completed") return;
      const stepEvent = event as StepCompletedEvent;
      const remaining = seenCounts.current.get(stepEvent.node_id) ?? 0;
      if (remaining > 0) {
        seenCounts.current.set(stepEvent.node_id, remaining - 1);
        return;
      }
      setLiveSteps((prev) => [
        ...prev,
        { nodeId: stepEvent.node_id, nodeType: stepEvent.node_type, durationMs: stepEvent.duration_ms, iterationKey: stepEvent.iteration_key },
      ]);
    })
      .then((close) => {
        if (cancelled) close();
        else unsubscribe = close;
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "живая трассировка недоступна"));

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [runId, live]);

  if (error) {
    return <p className="run-trace-view__error">{error}</p>;
  }

  if (!steps) {
    return (
      <div className="run-trace-view__loading">
        <Loader />
      </div>
    );
  }

  const entries: TraceEntry[] = [
    ...steps.map((step): TraceEntry => ({ kind: "static", branchId: step.branch_id ?? null, step })),
    ...liveSteps.map((entry): TraceEntry => ({ kind: "live", entry })),
  ];

  if (entries.length === 0) {
    return <p className="run-trace-view__empty">шагов в трассировке нет</p>;
  }

  return (
    <ol className="run-trace-view">
      {groupByBranch(entries).map((group, groupIndex) => (
        <li
          key={groupIndex}
          className={`run-trace-view__group${group.branchId ? " run-trace-view__group--branch" : ""}`}
        >
          {group.branchId ? <span className="run-trace-view__branch-label">Ветка {group.branchId}</span> : null}
          <ol className="run-trace-view__steps">
            {group.entries.map((entry, entryIndex) =>
              entry.kind === "static" ? (
                <li key={`static-${entry.step.node_id}-${entryIndex}`} className="run-trace-view__step">
                  <div className="run-trace-view__step-header">
                    <span className="run-trace-view__step-label">{displayLabel(entry.step.node_type)}</span>
                    <span className="run-trace-view__step-duration">{entry.step.duration_ms} мс</span>
                  </div>
                  <div className="run-trace-view__step-io">
                    <div className="run-trace-view__step-block">
                      <span className="run-trace-view__step-block-title">args</span>
                      <pre className="run-trace-view__step-pre">{JSON.stringify(entry.step.args, null, 2)}</pre>
                    </div>
                    <div className="run-trace-view__step-block">
                      <span className="run-trace-view__step-block-title">result</span>
                      <pre className="run-trace-view__step-pre">{JSON.stringify(entry.step.result, null, 2)}</pre>
                    </div>
                  </div>
                </li>
              ) : (
                <li
                  key={`live-${entry.entry.nodeId}-${entryIndex}`}
                  className="run-trace-view__step run-trace-view__step--live"
                >
                  <div className="run-trace-view__step-header">
                    <span className="run-trace-view__step-label">{displayLabel(entry.entry.nodeType)}</span>
                    <span className="run-trace-view__step-live-badge">live</span>
                    <span className="run-trace-view__step-duration">{entry.entry.durationMs} мс</span>
                  </div>
                </li>
              ),
            )}
          </ol>
        </li>
      ))}
    </ol>
  );
}
