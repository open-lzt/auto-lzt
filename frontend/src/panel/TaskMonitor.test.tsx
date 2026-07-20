import { ToastProvider } from "@open-lzt/ui";
import { render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { TaskMonitor } from "./TaskMonitor";
import type { Task, TaskHealth, TaskPage } from "../api/tasksClient";

/** The component reports action results through the kit's toast, whose hook throws outside its
 * provider — so the provider is part of the component's real mounting contract, not test scaffolding. */
function render(ui: ReactElement) {
  return rtlRender(<ToastProvider>{ui}</ToastProvider>);
}

const SERVER_TIME = "2026-07-20T12:00:00.000Z";

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: overrides.id ?? "t1",
    flow_id: overrides.flow_id ?? "f1",
    flow_name: overrides.flow_name ?? "Поднятие аккаунтов",
    schedule_cron: overrides.schedule_cron ?? "*/30 * * * *",
    active: overrides.active ?? true,
    health: overrides.health ?? "idle",
    next_fire_at: overrides.next_fire_at ?? "2026-07-20T12:05:00.000Z",
    last_run_at: overrides.last_run_at ?? "2026-07-20T11:30:00.000Z",
    last_run_status: overrides.last_run_status ?? "completed",
  };
}

function page(items: Task[], nextCursor: string | null = null): TaskPage {
  return { items, next_cursor: nextCursor, server_time: SERVER_TIME };
}

const fetchTasks = vi.fn();
const runTaskNow = vi.fn();

vi.mock("../api/tasksClient", async () => {
  const actual = await vi.importActual<typeof import("../api/tasksClient")>("../api/tasksClient");
  return {
    ...actual,
    fetchTasks: (...args: unknown[]) => fetchTasks(...args),
    runTaskNow: (...args: unknown[]) => runTaskNow(...args),
    createTaskStreamToken: vi.fn(async () => "tok"),
  };
});

vi.mock("./useTaskStream", () => ({ useTaskStream: () => "live" }));

describe("TaskMonitor", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "EventSource",
      class {
        close(): void {}
      },
    );
  });
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows skeletons on first load, not a spinner", async () => {
    let resolve!: (p: TaskPage) => void;
    fetchTasks.mockReturnValue(new Promise<TaskPage>((r) => (resolve = r)));

    const { container } = render(<TaskMonitor />);

    expect(container.querySelectorAll(".task-card--skeleton").length).toBeGreaterThan(0);
    resolve(page([task()]));
    await waitFor(() => expect(container.querySelectorAll(".task-card--skeleton")).toHaveLength(0));
  });

  const HEALTHS: TaskHealth[] = ["idle", "running", "failing", "paused"];
  it.each(HEALTHS)("renders a %s task", async (health) => {
    fetchTasks.mockResolvedValue(page([task({ health, active: health !== "paused" })]));

    const { container } = render(<TaskMonitor />);

    await waitFor(() => expect(container.querySelector(`.status-dot--${health}`)).not.toBeNull());
  });

  it("offers the action that fills the empty state", async () => {
    fetchTasks.mockResolvedValue(page([]));
    const onGoToBuilder = vi.fn();

    render(<TaskMonitor onGoToBuilder={onGoToBuilder} />);

    await screen.findByText(/Пока нет задач/);
    screen.getByRole("button", { name: "Собрать флоу" }).click();
    expect(onGoToBuilder).toHaveBeenCalled();
  });

  it("shows a real message on failure, never a raw stack", async () => {
    fetchTasks.mockRejectedValue(new Error("сервис недоступен"));

    render(<TaskMonitor />);

    await screen.findByText("сервис недоступен");
    expect(screen.queryByText(/at Object|Error:/)).toBeNull();
  });

  it("loads a second page without duplicating a card", async () => {
    // The keyset-paging bug this guards: a task that shifted position between the two reads comes
    // back on both pages, and a naive concat renders it twice.
    const first = task({ id: "t1", flow_id: "f1", flow_name: "Первая" });
    const second = task({ id: "t2", flow_id: "f2", flow_name: "Вторая" });
    fetchTasks
      .mockResolvedValueOnce(page([first, second], "cursor-1"))
      .mockResolvedValueOnce(page([second, task({ id: "t3", flow_id: "f3", flow_name: "Третья" })]));

    render(<TaskMonitor />);
    (await screen.findByRole("button", { name: "Показать ещё" })).click();

    await screen.findByText("Третья");
    expect(screen.getAllByText("Вторая")).toHaveLength(1);
  });

  it("stops offering more pages once the cursor runs out", async () => {
    fetchTasks.mockResolvedValue(page([task()], null));

    render(<TaskMonitor />);

    await screen.findByText("Поднятие аккаунтов");
    expect(screen.queryByRole("button", { name: "Показать ещё" })).toBeNull();
  });
});
