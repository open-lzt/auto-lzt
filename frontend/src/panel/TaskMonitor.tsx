import { Alert, Button, Empty, Skeleton, useToast } from "@open-lzt/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTasks, runTaskNow, type Task, type TaskEvent, type TaskPage } from "../api/tasksClient";
import { TaskCard } from "./TaskCard";
import { useTaskStream, type StreamState } from "./useTaskStream";
import "./panel.css";

const SKELETON_COUNT = 6;

const LIVE_LABEL: Record<StreamState, string> = {
  connecting: "подключение…",
  live: "в реальном времени",
  offline: "нет связи",
};

const LIVE_HINT: Record<StreamState, string> = {
  connecting: "Устанавливаем соединение для живых обновлений",
  live: "Обновления приходят в реальном времени",
  offline: "Соединение с сервером потеряно — данные могут устареть",
};

export interface TaskMonitorProps {
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

  // Ref, not state: putting tasks in the stream handler's closure forces a reconnect on every event.
  const knownFlowIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    knownFlowIds.current = new Set(tasks.map((t) => t.flow_id));
  }, [tasks]);

  const onEvent = useCallback((event: TaskEvent) => {
    if (!knownFlowIds.current.has(event.flow_id)) return;
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
        <span className={`panel-live panel-live--${streamState}`} title={LIVE_HINT[streamState]}>
          {LIVE_LABEL[streamState]}
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
