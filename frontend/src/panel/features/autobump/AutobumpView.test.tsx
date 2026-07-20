import { ToastProvider } from "@open-lzt/ui";
import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { AutobumpView } from "./AutobumpView";
import type { Account } from "../../../api/accountsClient";

const fetchAccounts = vi.fn();
const deployAutobump = vi.fn();

vi.mock("../../../api/accountsClient", async () => {
  const actual =
    await vi.importActual<typeof import("../../../api/accountsClient")>("../../../api/accountsClient");
  return { ...actual, fetchAccounts: () => fetchAccounts() };
});

vi.mock("./autobumpClient", () => ({
  deployAutobump: (settings: unknown) => deployAutobump(settings),
}));

function render(ui: ReactElement) {
  return rtlRender(<ToastProvider>{ui}</ToastProvider>);
}

function account(id: string, label: string, status: Account["status"] = "active"): Account {
  return { id, label, status, last_seen_at: null };
}

describe("AutobumpView", () => {
  afterEach(() => vi.clearAllMocks());

  it("cannot be deployed with no account selected", async () => {
    // Mirrors the server's NoAccountsSelected: the graph would compile, schedule, and then do
    // nothing every 30 minutes — far harder to notice than a disabled button.
    fetchAccounts.mockResolvedValue([account("a", "Первый")]);

    render(<AutobumpView />);

    await screen.findByText("Первый");
    expect(screen.getByRole("button", { name: "Включить поднятие" })).toBeDisabled();
  });

  it("sends exactly what the form shows", async () => {
    fetchAccounts.mockResolvedValue([account("a", "Первый"), account("b", "Второй")]);
    deployAutobump.mockResolvedValue({ flow_id: "f", trigger_id: "t" });

    render(<AutobumpView />);
    fireEvent.click(await screen.findByLabelText("Второй"));
    fireEvent.click(screen.getByRole("button", { name: "Каждый час" }));
    fireEvent.click(screen.getByRole("button", { name: "Включить поднятие" }));

    await waitFor(() =>
      expect(deployAutobump).toHaveBeenCalledWith({
        accounts: ["b"],
        scheduleCron: "0 * * * *",
        maxBumps: 20,
        reprice: false,
      }),
    );
  });

  it("offers only accounts that can actually run a bump", async () => {
    // An excluded account has a dead token; scheduling work onto it would fail every fire.
    fetchAccounts.mockResolvedValue([
      account("a", "Рабочий"),
      account("b", "Забанен", "excluded"),
    ]);

    render(<AutobumpView />);

    await screen.findByText("Рабочий");
    expect(screen.queryByText("Забанен")).toBeNull();
  });

  it("sends the operator to the tasks tab once the flow is live", async () => {
    fetchAccounts.mockResolvedValue([account("a", "Первый")]);
    deployAutobump.mockResolvedValue({ flow_id: "f", trigger_id: "t" });
    const onDeployed = vi.fn();

    render(<AutobumpView onDeployed={onDeployed} />);
    fireEvent.click(await screen.findByLabelText("Первый"));
    fireEvent.click(screen.getByRole("button", { name: "Включить поднятие" }));

    await waitFor(() => expect(onDeployed).toHaveBeenCalled());
  });

  it("points at the accounts tab when there is nothing to bump with", async () => {
    fetchAccounts.mockResolvedValue([]);

    render(<AutobumpView />);

    expect(await screen.findByText("Нет активных аккаунтов")).toBeInTheDocument();
  });

  it("keeps the per-run limit within what the endpoint accepts", async () => {
    // The endpoint bounds max_bumps to 1..1000; a form that can send 0 or 5000 turns a typo into a
    // 422 the operator has to decode.
    fetchAccounts.mockResolvedValue([account("a", "Первый")]);

    render(<AutobumpView />);
    const field = await screen.findByLabelText("Лотов за один запуск");
    fireEvent.change(field, { target: { value: "0" } });

    expect(field).toHaveValue("1");
  });
});
