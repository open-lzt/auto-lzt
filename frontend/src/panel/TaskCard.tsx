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

/**
 * The card column is ~280px and already carries an outcome badge, so the full
 * «20.07.2026, 16:02:07» ellipsised to «20.07.2026…» — the date survived and the CLOCK was what got
 * cut, which is the half that answers "how long ago did this break". Today's runs show the time
 * only; older ones keep a short date.
 *
 * `now` comes from the server, never `Date.now()`: a browser clock a day out would file today's
 * failure under yesterday and quietly show the wrong thing.
 */
function formatLastRun(iso: string | null, now: string): string {
  // Genderless on purpose: the subject is the flow's own name, and «ещё не запускалась» silently
  // assumed a feminine one — it read wrong beside «Автобай» and «Поднятие».
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

/** The next fire as a local wall-clock time — «в 15:00», or «завтра в 12:00» past midnight.
 *
 * Rendered in the browser's zone on purpose: the countdown says HOW LONG, this says WHEN, and
 * "when" is only useful in the zone the operator lives in. */
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
        {/* The wall-clock moment, in the operator's OWN zone. Schedules run in UTC, so a
            «9:00» task fires at 12:00 for someone at UTC+3 — a countdown alone never reveals
            that, and the mismatch reads as a broken clock rather than a timezone. */}
        {!paused && task.next_fire_at ? (
          <span className="task-card__countdown-at">{formatFireAt(task.next_fire_at)}</span>
        ) : null}
      </div>

      <dl className="task-card__meta">
        <div className="task-card__meta-row">
          <dt>Расписание</dt>
          {/* The words, not the cron. The server sends both and takes the phrase from the same map
              the schedule picker offers, so the card and the form call an interval by one name.
              The raw expression stays in the tooltip for whoever wants it. */}
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
        {busy ? "Запускаю…" : "Поднять сейчас"}
      </Button>
    </Card>
  );
}
