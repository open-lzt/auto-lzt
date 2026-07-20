import { request } from "./flowClient";

export type AccountStatus = "active" | "excluded";

export interface Account {
  id: string;
  status: AccountStatus;
  label: string | null;
  last_seen_at: string | null;
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
