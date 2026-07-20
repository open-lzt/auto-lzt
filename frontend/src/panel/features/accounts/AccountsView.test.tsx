import { ToastProvider } from "@open-lzt/ui";
import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { AccountsView } from "./AccountsView";
import type { Account } from "../../../api/accountsClient";

const fetchAccounts = vi.fn();
const deleteAccount = vi.fn();
const addAccount = vi.fn();

vi.mock("../../../api/accountsClient", async () => {
  const actual =
    await vi.importActual<typeof import("../../../api/accountsClient")>("../../../api/accountsClient");
  return {
    ...actual,
    fetchAccounts: () => fetchAccounts(),
    deleteAccount: (id: string) => deleteAccount(id),
    addAccount: (token: string) => addAccount(token),
    setAccountLabel: vi.fn(),
    reactivateAccount: vi.fn(),
  };
});

function render(ui: ReactElement) {
  return rtlRender(<ToastProvider>{ui}</ToastProvider>);
}

function account(overrides: Partial<Account> = {}): Account {
  return {
    id: overrides.id ?? "11111111-1111-1111-1111-111111111111",
    status: overrides.status ?? "active",
    label: overrides.label ?? "Основной",
    last_seen_at: overrides.last_seen_at ?? "2026-07-20T10:00:00.000Z",
  };
}

describe("AccountsView", () => {
  afterEach(() => vi.clearAllMocks());

  it("names the blocking flows when a delete is refused", async () => {
    // The payoff of AccountInUse being a typed error carrying flow_names: the operator is told
    // WHICH task still pins the account, not merely that deletion failed.
    fetchAccounts.mockResolvedValue([account()]);
    deleteAccount.mockRejectedValue(
      new Error("Аккаунт используется в активных задачах: Поднятие, Ночной релист"),
    );

    render(<AccountsView />);
    fireEvent.click(await screen.findByRole("button", { name: "Удалить" }));

    expect(await screen.findByText(/Поднятие, Ночной релист/)).toBeInTheDocument();
  });

  it("keeps a rejected token in the field so a paste error can be fixed", async () => {
    fetchAccounts.mockResolvedValue([]);
    addAccount.mockRejectedValue(new Error("токен недействителен"));

    render(<AccountsView />);
    const field = await screen.findByLabelText("Токен lzt.market");
    fireEvent.change(field, { target: { value: "bad-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

    await screen.findByText("токен недействителен");
    expect(field).toHaveValue("bad-token");
  });

  it("clears the field once the token is accepted", async () => {
    fetchAccounts.mockResolvedValue([]);
    addAccount.mockResolvedValue(account());

    render(<AccountsView />);
    const field = await screen.findByLabelText("Токен lzt.market");
    fireEvent.change(field, { target: { value: "good-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

    await waitFor(() => expect(field).toHaveValue(""));
  });

  it("offers to bring back an excluded account, and only an excluded one", async () => {
    fetchAccounts.mockResolvedValue([
      account({ id: "a", status: "excluded", label: "Забанен" }),
      account({ id: "b", status: "active", label: "Рабочий" }),
    ]);

    render(<AccountsView />);

    await screen.findByText("Исключён");
    expect(screen.getAllByRole("button", { name: "Вернуть" })).toHaveLength(1);
  });

  it("shows a designed empty state rather than a bare table", async () => {
    fetchAccounts.mockResolvedValue([]);

    render(<AccountsView />);

    expect(await screen.findByText("Пока нет аккаунтов")).toBeInTheDocument();
  });

  it("renders the label as an editable field, not static text", async () => {
    // Renaming is the one thing here that is safe to get wrong, so it is edited in place; a modal
    // for a single text input would be friction with no payoff.
    fetchAccounts.mockResolvedValue([account()]);

    render(<AccountsView />);

    expect(await screen.findByDisplayValue("Основной")).toBeInTheDocument();
  });
});
