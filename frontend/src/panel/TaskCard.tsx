import { Badge, Button, Card } from "@open-lzt/ui";
import { Countdown } from "../ui/Countdown";
import { StatusDot } from "../ui/StatusDot";
import type { Task } from "../api/tasksClient";

export interface TaskCardProps {
  task: Task;
  serverTime: string;
  onRunNow: (taskId: string) => void;
  busy?: boolean;
  /** Index in the grid, used only to stagger the entrance. */
  index?: number;
}

const OUTCOME_TONE = {
  completed: "brand",
  failed: "danger",
  running: "info",
  pending: "default",
} as const;

const OUTCOME_LABEL = {
  completed: "Успешно",
  failed: "Ошибка",
  running: "Идёт",
  pending: "В очереди",
} as const;

function formatLastRun(iso: string | null): string {
  if (iso === null) return "ещё не запускалась";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleString("ru-RU");
}

export function TaskCard({ task, serverTime, onRunNow, busy = false, index = 0 }: TaskCardProps) {
  const paused = !task.active;

  return (
    <Card
      hover
      className="task-card"
      // Staggered by index rather than by a per-card timer: the delay is a static style, so the
      // browser animates it off the main thread and a grid of twenty cards costs no timers at all.
      style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
    >
      <div className="task-card__head">
        <StatusDot health={task.health} />
        <h3 className="task-card__name" title={task.flow_name}>
          {task.flow_name}
        </h3>
      </div>

      {/* The focal point. Everything else on the card is deliberately quieter than this. */}
      <div className="task-card__countdown">
        <Countdown targetAt={paused ? null : task.next_fire_at} serverTime={serverTime} />
        <span className="task-card__countdown-label">
          {paused ? "на паузе" : "до следующего запуска"}
        </span>
      </div>

      <dl className="task-card__meta">
        <div className="task-card__meta-row">
          <dt>Расписание</dt>
          <dd>
            <code className="task-card__cron">{task.schedule_cron}</code>
          </dd>
        </div>
        <div className="task-card__meta-row">
          <dt>Последний запуск</dt>
          <dd>
            {task.last_run_status ? (
              <Badge tone={OUTCOME_TONE[task.last_run_status]} pill>
                {OUTCOME_LABEL[task.last_run_status]}
              </Badge>
            ) : null}
            <span className="task-card__last-run">{formatLastRun(task.last_run_at)}</span>
          </dd>
        </div>
      </dl>

      <Button
        variant="outline"
        size="sm"
        block
        loading={busy}
        disabled={busy || paused}
        onClick={() => onRunNow(task.id)}
      >
        {busy ? "Запускаю…" : "Поднять сейчас"}
      </Button>
    </Card>
  );
}
