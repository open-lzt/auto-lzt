import { Alert, Button, Empty, Skeleton, useToast } from "@open-lzt/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTasks, runTaskNow, type Task, type TaskEvent, type TaskPage } from "../api/tasksClient";
import { TaskCard } from "./TaskCard";
import { useTaskStream } from "./useTaskStream";
import "./panel.css";

const SKELETON_COUNT = 6;

export interface TaskMonitorProps {
  /** Offered by the empty state — the panel cannot create a flow itself. */
  onGoToBuilder?: () => void;
}

export function TaskMonitor({ onGoToBuilder }: TaskMonitorProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [serverTime, setServerTime] = useState<string>(() => new Date().toISOString());
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const toast = useToast();

  const applyPage = useCallback((page: TaskPage, append: boolean) => {
    setServerTime(page.server_time);
    setCursor(page.next_cursor);
    setTasks((prev) => {
      if (!append) return page.items;
      // Keyed by id rather than concatenated: a task that changed position between pages would
      // otherwise appear twice, which is the classic keyset-paging duplicate.
      const seen = new Set(prev.map((t) => t.id));
      return [...prev, ...page.items.filter((t) => !seen.has(t.id))];
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchTasks()
      .then((page) => {
        if (!cancelled) applyPage(page, false);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "не удалось загрузить задачи");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applyPage]);

  // A ref, not state: the stream handler needs to know which flows are on screen, and reading that
  // from state would put `tasks` in the handler's closure — which is what forces a reconnect on
  // every event. See useTaskStream's note on closure discipline.
  const knownFlowIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    knownFlowIds.current = new Set(tasks.map((t) => t.flow_id));
  }, [tasks]);

  const onEvent = useCallback((event: TaskEvent) => {
    if (!knownFlowIds.current.has(event.flow_id)) return;
    // The event says a card is stale, not what it should now say. Re-reading the page is one
    // bounded query and keeps the card's several derived fields (health, next fire, last outcome)
    // consistent with each other, which patching them field-by-field on the client would not.
    void fetchTasks().then((page) => applyPage(page, false));
  }, [applyPage]);

  const streamState = useTaskStream(onEvent);

  async function handleRunNow(taskId: string): Promise<void> {
    setBusyTaskId(taskId);
    try {
      await runTaskNow(taskId);
      toast.show("Запуск поставлен в очередь");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "неизвестная ошибка";
      toast.show(`Не удалось запустить: ${detail}`, { tone: "danger" });
    } finally {
      setBusyTaskId(null);
    }
  }

  async function handleLoadMore(): Promise<void> {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      applyPage(await fetchTasks(cursor), true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось догрузить страницу");
    } finally {
      setLoadingMore(false);
    }
  }

  if (loading) {
    return (
      <div className="panel-view">
        <div className="task-grid" aria-busy="true" aria-label="Загрузка задач">
          {Array.from({ length: SKELETON_COUNT }, (_, i) => (
            <Skeleton key={i} className="task-card task-card--skeleton" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel-view">
        <Alert tone="danger" title="Задачи не загрузились">
          {error}
        </Alert>
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div className="panel-view">
        <Empty title="Пока нет задач по расписанию">
          <p className="panel-empty__hint">
            Задача появляется здесь, когда у флоу есть расписание. Соберите флоу и задайте ему
            триггер «по расписанию».
          </p>
          {onGoToBuilder ? (
            <Button variant="primary" onClick={onGoToBuilder}>
              Собрать флоу
            </Button>
          ) : null}
        </Empty>
      </div>
    );
  }

  return (
    <div className="panel-view">
      <div className="panel-view__head">
        <h2 className="panel-view__title">Задачи</h2>
        <span
          className={`panel-live panel-live--${streamState}`}
          title={
            streamState === "live"
              ? "Обновления приходят в реальном времени"
              : "Соединение с сервером потеряно — данные могут устареть"
          }
        >
          {streamState === "live" ? "в реальном времени" : "нет связи"}
        </span>
      </div>

      <div className="task-grid">
        {tasks.map((task, i) => (
          <TaskCard
            key={task.id}
            task={task}
            index={i}
            serverTime={serverTime}
            busy={busyTaskId === task.id}
            onRunNow={(id) => void handleRunNow(id)}
          />
        ))}
      </div>

      {cursor ? (
        <div className="task-grid__more">
          <Button variant="ghost" loading={loadingMore} onClick={() => void handleLoadMore()}>
            Показать ещё
          </Button>
        </div>
      ) : null}
    </div>
  );
}
