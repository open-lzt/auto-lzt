import { Select } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { accountBalance, fetchAccounts, type Account } from "../api/accountsClient";
import "./account-pin.css";

export interface AccountPinProps {
  value: string | null;
  onChange: (accountId: string | null) => void;
  overriddenByLoop: boolean;
}

// Pins NodeSpec.account_ref (single UUID). Not AccountMultiPicker, which writes a JSON array input.
export function AccountPin({ value, onChange, overriddenByLoop }: AccountPinProps) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetchAccounts()
      .then((all) => setAccounts(all.filter((a) => a.status === "active")))
      .catch(() => setFailed(true));
  }, []);

  return (
    <div className="account-pin">
      <div className="account-pin__head">
        <span className="account-pin__label">Аккаунт</span>
        {value ? (
          <button type="button" className="account-pin__clear" onClick={() => onChange(null)}>
            Убрать пин
          </button>
        ) : null}
      </div>

      {failed ? <p className="account-pin__note">не удалось загрузить аккаунты</p> : null}
      {!failed && accounts === null ? <p className="account-pin__note">загрузка…</p> : null}

      {accounts !== null && accounts.length === 0 ? (
        <p className="account-pin__note">нет активных аккаунтов — добавьте на вкладке «Аккаунты»</p>
      ) : null}

      {accounts !== null && accounts.length > 0 ? (
        <Select
          size="sm"
          className="account-pin__select"
          value={value ?? ""}
          placeholder="Выбрать аккаунт"
          aria-label="Аккаунт для этого блока"
          onChange={(next) => onChange(next === "" ? null : next)}
          options={accounts.map((account) => {
            const name = account.label ?? account.username ?? account.id.slice(0, 8);
            const balance = accountBalance(account);
            return { value: account.id, label: balance ? `${name} · ${balance}` : name };
          })}
        />
      ) : null}

      {overriddenByLoop ? (
        <p className="account-pin__hint">
          Блок внутри цикла по аккаунтам — цикл подставит свой аккаунт, этот пин не применится.
        </p>
      ) : null}
    </div>
  );
}
