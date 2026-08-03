import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as accountsClient from "../api/accountsClient";
import type { Account } from "../api/accountsClient";
import { AccountPin } from "./AccountPin";

function account(over: Partial<Account> = {}): Account {
  return {
    id: "acc-1",
    status: "active",
    label: "Основной",
    username: "seller",
    balance: "1200.00",
    balance_currency: "RUB",
    last_seen_at: null,
    profile_synced_at: null,
    ...over,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("AccountPin", () => {
  it("offers only active accounts and reports the chosen id", async () => {
    vi.spyOn(accountsClient, "fetchAccounts").mockResolvedValue([
      account(),
      account({ id: "acc-2", status: "excluded", label: "Исключённый" }),
    ]);
    const onChange = vi.fn();

    render(<AccountPin value={null} onChange={onChange} overriddenByLoop={false} />);

    const select = await screen.findByLabelText("Аккаунт для этого блока");
    expect(screen.queryByText(/Исключённый/)).not.toBeInTheDocument();

    await userEvent.click(select);
    await userEvent.click(screen.getByRole("option", { name: /Основной/ }));
    expect(onChange).toHaveBeenCalledWith("acc-1");
  });

  it("reports null when the pin is cleared", async () => {
    vi.spyOn(accountsClient, "fetchAccounts").mockResolvedValue([account()]);
    const onChange = vi.fn();

    render(<AccountPin value="acc-1" onChange={onChange} overriddenByLoop={false} />);

    fireEvent.click(await screen.findByRole("button", { name: "Убрать пин" }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("leads somewhere when there are no active accounts", async () => {
    // An empty internal panel is a first-run screen: without the next action it is a dead end.
    vi.spyOn(accountsClient, "fetchAccounts").mockResolvedValue([]);

    render(<AccountPin value={null} onChange={vi.fn()} overriddenByLoop={false} />);

    expect(await screen.findByText(/добавьте на вкладке «Аккаунты»/)).toBeInTheDocument();
  });

  it("says the pin will not apply inside a loop instead of hiding it", async () => {
    vi.spyOn(accountsClient, "fetchAccounts").mockResolvedValue([account()]);

    render(<AccountPin value="acc-1" onChange={vi.fn()} overriddenByLoop />);

    expect(await screen.findByText(/цикл подставит свой аккаунт/)).toBeInTheDocument();
  });

  it("says so when the account list cannot be loaded", async () => {
    vi.spyOn(accountsClient, "fetchAccounts").mockRejectedValue(new Error("сеть"));

    render(<AccountPin value={null} onChange={vi.fn()} overriddenByLoop={false} />);

    expect(await screen.findByText("не удалось загрузить аккаунты")).toBeInTheDocument();
  });
});
