import { request } from "./flowClient";

export type AccountStatus = "active" | "excluded";

export interface Account {
  id: string;
  status: AccountStatus;
  label: string | null;
  last_seen_at: string | null;
  /** Marketplace profile, cached server-side. All null until the first successful refresh — a
   * token that never authenticated has no profile, and a blank says so instead of showing 0. */
  username: string | null;
  /** A string, not a number: it is money, and parsing it to a JS float would reintroduce the
   * rounding the backend uses Decimal to avoid. Format it; never do arithmetic on it. */
  balance: string | null;
  balance_currency: string | null;
  profile_synced_at: string | null;
}

/** What to call this account on screen, best name first.
 *
 * Shared rather than inlined per screen: the accounts tab and every account picker have to agree,
 * and they previously did not — one showed «Без названия», the other «72f48cfc», with nothing on
 * either saying they were the same row. */
export function accountName(account: Account): string {
  return account.label ?? account.username ?? account.id.slice(0, 8);
}

/** The balance as text, or null when it has never been fetched. Never renders an amount without
 * its currency: a bare number under the wrong sign is worse than no number. */
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

/** Refused with 409 while a live schedule still pins the account; the error message names the
 * flows that are blocking it, which is the whole point of the delete being guarded. */
export async function deleteAccount(id: string): Promise<void> {
  await request<{ id: string; deleted: boolean }>(`/accounts/${id}/delete`, { method: "POST" });
}

export async function reactivateAccount(id: string): Promise<Account> {
  return request<Account>(`/accounts/${id}/reactivate`, { method: "POST" });
}

/** Re-read nickname and balance from the marketplace. Separate from `fetchAccounts` on purpose:
 * listing accounts must not depend on the marketplace being reachable. */
export async function refreshAccountProfile(id: string): Promise<Account> {
  return request<Account>(`/accounts/${id}/refresh`, { method: "POST" });
}
