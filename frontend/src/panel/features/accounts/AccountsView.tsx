import { Alert, Badge, Button, Card, Empty, Icon, Input, Skeleton, useToast } from "@open-lzt/ui";
import { useCallback, useEffect, useState } from "react";
import {
  accountBalance,
  addAccount,
  deleteAccount,
  fetchAccounts,
  reactivateAccount,
  refreshAccountProfile,
  setAccountLabel,
  type Account,
} from "../../../api/accountsClient";
import "./accounts.css";

function formatLastSeen(iso: string | null): string {
  if (iso === null) return "ещё не использовался";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleString("ru-RU");
}

export function AccountsView() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const toast = useToast();

  const reload = useCallback(async () => {
    try {
      setAccounts(await fetchAccounts());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "не удалось загрузить аккаунты");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function handleAdd(): Promise<void> {
    if (!token.trim()) return;
    setAdding(true);
    try {
      await addAccount(token.trim());
      // Cleared on success only: a rejected token stays in the field so the operator can fix a
      // paste error instead of digging it out again.
      setToken("");
      toast.show("Аккаунт добавлен");
      await reload();
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось добавить", { tone: "danger" });
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(account: Account): Promise<void> {
    setBusyId(account.id);
    try {
      await deleteAccount(account.id);
      toast.show("Аккаунт удалён");
      await reload();
    } catch (err) {
      // The 409 already names the flows still pinning this account — showing it verbatim is the
      // payoff of the guard being a typed error rather than a bare refusal.
      toast.show(err instanceof Error ? err.message : "не удалось удалить", { tone: "danger" });
    } finally {
      setBusyId(null);
    }
  }

  async function handleRename(account: Account, label: string): Promise<void> {
    const next = label.trim() || null;
    if (next === account.label) return;
    try {
      await setAccountLabel(account.id, next);
      await reload();
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось переименовать", {
        tone: "danger",
      });
    }
  }

  async function handleRefresh(account: Account): Promise<void> {
    setBusyId(account.id);
    try {
      await refreshAccountProfile(account.id);
      await reload();
    } catch (err) {
      // Named per-account: with several rows on screen, "не удалось обновить" alone leaves the
      // operator guessing which token is the bad one.
      toast.show(
        err instanceof Error ? err.message : `не удалось обновить ${account.id.slice(0, 8)}`,
        { tone: "danger" },
      );
    } finally {
      setBusyId(null);
    }
  }

  async function handleReactivate(account: Account): Promise<void> {
    setBusyId(account.id);
    try {
      await reactivateAccount(account.id);
      toast.show("Аккаунт снова активен");
      await reload();
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось вернуть", { tone: "danger" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="panel-view">
      <div className="panel-view__head">
        <h2 className="panel-view__title">Аккаунты</h2>
      </div>

      <Card className="accounts-add">
        <label className="accounts-add__label" htmlFor="account-token">
          Токен lzt.market
        </label>
        <div className="accounts-add__row">
          <Input
            id="account-token"
            type="password"
            value={token}
            placeholder="Вставьте токен"
            autoComplete="off"
            onChange={(e) => setToken(e.target.value)}
          />
          <Button variant="primary" loading={adding} disabled={!token.trim()} onClick={() => void handleAdd()}>
            Добавить
          </Button>
        </div>
        <p className="accounts-add__hint">
          Токен шифруется на сервере и больше никогда не показывается — ни в интерфейсе, ни в логах.
        </p>
      </Card>

      {error ? (
        <Alert tone="danger" title="Аккаунты не загрузились">
          {error}
        </Alert>
      ) : null}

      {accounts === null && !error ? (
        <div className="accounts-list">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="accounts-row accounts-row--skeleton" />
          ))}
        </div>
      ) : null}

      {accounts !== null && accounts.length === 0 ? (
        <Empty title="Пока нет аккаунтов">
          <p className="panel-empty__hint">
            Поднятие и любые действия на маркете выполняются от имени аккаунта. Добавьте первый.
          </p>
        </Empty>
      ) : null}

      {accounts !== null && accounts.length > 0 ? (
        <div className="accounts-list">
          {accounts.map((account) => (
            <Card key={account.id} className="accounts-row">
              <input
                className="accounts-row__label"
                defaultValue={account.label ?? ""}
                placeholder="Без названия"
                aria-label="Название аккаунта"
                onBlur={(e) => void handleRename(account, e.target.value)}
              />
              {/* The nickname is the account's real name. The id prefix survives only as the
                  fallback for a token whose profile has never been fetched, so a row is never
                  anonymous — and the account pickers use the same rule, so the screens agree. */}
              <span className="accounts-row__username" title={account.id}>
                {account.username ?? account.id.slice(0, 8)}
              </span>
              <span className="accounts-row__balance">
                {accountBalance(account) ?? <span className="accounts-row__muted">—</span>}
              </span>
              <Badge tone={account.status === "active" ? "brand" : "warning"} pill>
                {account.status === "active" ? "Активен" : "Исключён"}
              </Badge>
              <span className="accounts-row__seen">{formatLastSeen(account.last_seen_at)}</span>
              <div className="accounts-row__actions">
                <Button
                  variant="ghost"
                  size="sm"
                  loading={busyId === account.id}
                  aria-label="Обновить ник и баланс"
                  title={
                    account.profile_synced_at
                      ? `Обновлено ${new Date(account.profile_synced_at).toLocaleString("ru-RU")}`
                      : "Ник и баланс ещё не загружались"
                  }
                  onClick={() => void handleRefresh(account)}
                >
                  <Icon name="refresh" size={14} />
                </Button>
                {account.status === "excluded" ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={busyId === account.id}
                    onClick={() => void handleReactivate(account)}
                  >
                    Вернуть
                  </Button>
                ) : null}
                <Button
                  variant="ghost"
                  size="sm"
                  loading={busyId === account.id}
                  onClick={() => void handleDelete(account)}
                >
                  Удалить
                </Button>
              </div>
            </Card>
          ))}
        </div>
      ) : null}
    </div>
  );
}
