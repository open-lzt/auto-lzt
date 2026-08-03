import { request } from "./httpClient";

export type AccountStatus = "active" | "excluded";

export interface Account {
  id: string;
  status: AccountStatus;
  label: string | null;
  last_seen_at: string | null;
  username: string | null;
  /** Money as a string — parsing to a float reintroduces the rounding Decimal avoids server-side. */
  balance: string | null;
  balance_currency: string | null;
  profile_synced_at: string | null;
}

export function accountName(account: Account): string {
  return account.label ?? account.username ?? account.id.slice(0, 8);
}

export function accountBalance(account: Account): string | null {
  if (account.balance === null) return null;
  const amount = Number(account.balance);
  const shown = Number.isFinite(amount)
    ? amount.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : account.balance;
  return account.balance_currency ? `${shown} ${account.balance_currency}` : shown;
}

export async function fetchAccounts(): Promise<Account[]> {
  return request<Account[]>("/accounts/list");
}

export async function addAccount(token: string): Promise<Account> {
  return request<Account>("/accounts/create", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function setAccountLabel(id: string, label: string | null): Promise<Account> {
  return request<Account>(`/accounts/${id}/label`, {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

export async function deleteAccount(id: string): Promise<void> {
  await request<{ id: string; deleted: boolean }>(`/accounts/${id}/delete`, { method: "POST" });
}

export async function reactivateAccount(id: string): Promise<Account> {
  return request<Account>(`/accounts/${id}/reactivate`, { method: "POST" });
}

export async function refreshAccountProfile(id: string): Promise<Account> {
  return request<Account>(`/accounts/${id}/refresh`, { method: "POST" });
}
