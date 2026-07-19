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
  /** Render the Russian label beside the dot. Off inside dense rows, on where it stands alone. */
  withLabel?: boolean;
  className?: string;
}

/**
 * A task's health, as a dot.
 *
 * Only `running` animates. A pulse means "something is happening right now" — putting one on
 * `failing` too would make a stuck task look busy, which is the opposite of what the operator needs
 * to see, and a screen of pulsing dots stops carrying information at all.
 *
 * Colour alone never carries the state: every dot has a `title` and, under
 * `prefers-reduced-motion`, the four states still separate by hue and by that label even with the
 * animation suppressed.
 */
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
