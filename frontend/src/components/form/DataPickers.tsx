import { Checkbox } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { accountBalance, fetchAccounts, type Account } from "../../api/accountsClient";
import { fetchCategories, type MarketCategoryDTO } from "../../api/catalogClient";
import { OptionPicker } from "./controls";
import "./data-pickers.css";

interface PickerProps {
  /** JSON-encoded array of selected ids — the same wire shape as MultiSelect. */
  value: string;
  onChange: (value: string) => void;
}

function parseSelected(value: string): string[] {
  try {
    const parsed: unknown = value ? JSON.parse(value) : [];
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

// Fetches its own options: the field is declared server-side as `x-ui.widget: account_ref`, so
// nothing between the schema and this component otherwise knows to go get accounts.
export function AccountMultiPicker({ value, onChange }: PickerProps) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [failed, setFailed] = useState(false);
  const selected = parseSelected(value);

  useEffect(() => {
    fetchAccounts()
      .then((all) => setAccounts(all.filter((a) => a.status === "active")))
      .catch(() => setFailed(true));
  }, []);

  function toggle(id: string): void {
    const next = selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id];
    onChange(JSON.stringify(next));
  }

  if (failed) return <p className="data-picker__note">не удалось загрузить аккаунты</p>;
  if (accounts === null) return <p className="data-picker__note">загрузка…</p>;
  if (accounts.length === 0) {
    return <p className="data-picker__note">нет активных аккаунтов — добавьте на вкладке «Аккаунты»</p>;
  }

  return (
    <div className="data-picker">
      {accounts.map((account) => (
        <label key={account.id} className="data-picker__row">
          <Checkbox
            checked={selected.includes(account.id)}
            onChange={() => toggle(account.id)}
          />
          <span className="data-picker__name">
            {account.label ?? account.username ?? account.id.slice(0, 8)}
          </span>
          {account.label && account.username ? (
            <span className="data-picker__sub">{account.username}</span>
          ) : null}
          {accountBalance(account) ? (
            <span className="data-picker__balance" title="Баланс">
              {accountBalance(account)}
            </span>
          ) : null}
        </label>
      ))}
    </div>
  );
}

// Fetched live from GET /catalog/categories (same enum the search node validates against) —
// hand-copied slugs used to drift whenever the marketplace gained a category.
export function CategorySelect({ value, onChange }: PickerProps) {
  const [categories, setCategories] = useState<MarketCategoryDTO[] | null>(null);

  useEffect(() => {
    fetchCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  return (
    <OptionPicker
      value={value}
      onChange={onChange}
      options={(categories ?? []).map((c) => ({ value: c.slug, label: c.label }))}
      placeholder={categories === null ? "загрузка…" : "выберите категорию…"}
    />
  );
}
