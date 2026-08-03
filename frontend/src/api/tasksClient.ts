import { request } from "./httpClient";

/** Mirrors `TaskHealth` in app/domain/tasks/model.py. */
export type TaskHealth = "idle" | "running" | "failing" | "paused";

export type TaskRunStatus = "pending" | "running" | "completed" | "failed";

export interface Task {
  id: string;
  flow_id: string;
  flow_name: string;
  schedule_cron: string;
  /** Resolved server-side; falls back to the raw cron for a flow edited on the canvas. */
  schedule_label: string;
  active: boolean;
  health: TaskHealth;
  next_fire_at: string | null;
  last_run_at: string | null;
  last_run_status: TaskRunStatus | null;
}

export interface TaskPage {
  items: Task[];
  next_cursor: string | null;
  /** Every countdown anchors on this, never the browser's clock — it can be minutes off. */
  server_time: string;
}

export interface RunNowResponse {
  run_id: string;
  task_id: string;
}

/** Mirrors `TaskEventReason` in app/domain/flow_engine/events.py. */
export type TaskEventReason = "run_started" | "run_finished" | "task_changed";

export interface TaskEvent {
  type: "task";
  reason: TaskEventReason;
  flow_id: string;
  /** Absent when the worker knows only which flow ran, not the triggering schedule. */
  task_id?: string | null;
  status?: TaskRunStatus | null;
  occurred_at?: string;
}

interface StreamTokenResponse {
  token: string;
  expires_in: number;
}

export async function fetchTasks(cursor?: string | null, limit = 20): Promise<TaskPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return request<TaskPage>(`/tasks/list?${params.toString()}`);
}

export async function runTaskNow(taskId: string): Promise<RunNowResponse> {
  return request<RunNowResponse>(`/tasks/${taskId}/run-now`, { method: "POST" });
}

export async function createTaskStreamToken(): Promise<string> {
  const { token } = await request<StreamTokenResponse>("/tasks/stream-token", { method: "POST" });
  return token;
}

export function taskStreamUrl(token: string): string {
  return `/api/tasks/stream?token=${encodeURIComponent(token)}`;
}
