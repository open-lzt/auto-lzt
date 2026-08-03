import { Badge, Button, Card } from "@open-lzt/ui";
import { Countdown } from "../ui/Countdown";
import { StatusDot } from "../ui/StatusDot";
import type { Task } from "../api/tasksClient";

export interface TaskCardProps {
  task: Task;
  serverTime: string;
  onRunNow: (taskId: string) => void;
  busy?: boolean;
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

/** `now` comes from the server, never `Date.now()` — a skewed browser clock would misdate today's run. */
function formatLastRun(iso: string | null, now: string): string {
  if (iso === null) return "запусков не было";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "—";
  const time = at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const today = new Date(now);
  const sameDay =
    at.getFullYear() === today.getFullYear() &&
    at.getMonth() === today.getMonth() &&
    at.getDate() === today.getDate();
  if (sameDay) return time;
  return `${at.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })}, ${time}`;
}

/** Local wall-clock time — «в 15:00», or «завтра в 12:00» past midnight. */
function formatFireAt(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const now = new Date();
  const tomorrow = at.getDate() !== now.getDate() || at.getMonth() !== now.getMonth();
  return tomorrow ? `завтра в ${time}` : `в ${time}`;
}

export function TaskCard({ task, serverTime, onRunNow, busy = false, index = 0 }: TaskCardProps) {
  const paused = !task.active;

  return (
    <Card
      hover
      className="task-card"
      style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
    >
      <div className="task-card__head">
        <StatusDot health={task.health} />
        <h3 className="task-card__name" title={task.flow_name}>
          {task.flow_name}
        </h3>
      </div>

      <div className="task-card__countdown">
        <Countdown targetAt={paused ? null : task.next_fire_at} serverTime={serverTime} />
        <span className="task-card__countdown-label">
          {paused ? "на паузе" : "до следующего запуска"}
        </span>
        {/* Schedules run in UTC — a «9:00» task fires at 12:00 for someone at UTC+3. */}
        {!paused && task.next_fire_at ? (
          <span className="task-card__countdown-at">{formatFireAt(task.next_fire_at)}</span>
        ) : null}
      </div>

      <dl className="task-card__meta">
        <div className="task-card__meta-row">
          <dt>Расписание</dt>
          <dd title={task.schedule_cron}>{task.schedule_label}</dd>
        </div>
        <div className="task-card__meta-row">
          <dt>Последний запуск</dt>
          <dd>
            {task.last_run_status ? (
              <Badge tone={OUTCOME_TONE[task.last_run_status]} pill>
                {OUTCOME_LABEL[task.last_run_status]}
              </Badge>
            ) : null}
            <span className="task-card__last-run">
              {formatLastRun(task.last_run_at, serverTime)}
            </span>
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
        {busy ? "Запускаю…" : "Запустить сейчас"}
      </Button>
    </Card>
  );
}
