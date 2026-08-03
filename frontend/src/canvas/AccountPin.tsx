import { useEffect, useState } from "react";
import { accountBalance, fetchAccounts, type Account } from "../api/accountsClient";
import "./account-pin.css";

export interface AccountPinProps {
  value: string | null;
  onChange: (accountId: string | null) => void;
  /** The node sits inside a for_each_account loop, so the runtime supplies an account per iteration
   * and this pin, if set, is ignored. Shown rather than hidden: hiding it would leave a pin the
   * operator set earlier silently in the spec. */
  overriddenByLoop: boolean;
}

/** Pins ONE account to a node — NodeSpec.account_ref, which is a single UUID at the top of the node
 * and not one of its inputs.
 *
 * Deliberately not AccountMultiPicker: that one writes a JSON array into an input field, because it
 * feeds for_each_account's list. Same data source, different destination and cardinality. */
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
        <select
          className="account-pin__select"
          value={value ?? ""}
          aria-label="Аккаунт для этого блока"
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        >
          <option value="">Выбрать аккаунт</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {`${account.label ?? account.username ?? account.id.slice(0, 8)} · ${accountBalance(account) ?? "—"}`}
            </option>
          ))}
        </select>
      ) : null}

      {overriddenByLoop ? (
        <p className="account-pin__hint">
          Блок внутри цикла по аккаунтам — цикл подставит свой аккаунт, этот пин не применится.
        </p>
      ) : null}
    </div>
  );
}
