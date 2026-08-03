import "./status-dot.css";

/** Mirrors `TaskHealth` in `app/domain/tasks/model.py`. */
export type TaskHealth = "idle" | "running" | "failing" | "paused";

const LABELS: Record<TaskHealth, string> = {
  idle: "Ожидает",
  running: "Выполняется",
  failing: "Ошибка",
  paused: "На паузе",
};

export interface StatusDotProps {
  health: TaskHealth;
  withLabel?: boolean;
  className?: string;
}

/** Colour alone never carries the state — every dot has a `title` and a text label for
 * `prefers-reduced-motion` / screen readers. */
export function StatusDot({ health, withLabel = false, className }: StatusDotProps) {
  const label = LABELS[health];
  return (
    <span
      className={["status-dot-wrap", className].filter(Boolean).join(" ")}
      title={label}
      role="status"
    >
      <span className={`status-dot status-dot--${health}`} aria-hidden="true" />
      {withLabel ? <span className="status-dot__label">{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  );
}
