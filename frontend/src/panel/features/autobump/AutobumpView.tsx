import { Alert, Badge, Button, Card, Empty, Input, useToast } from "@open-lzt/ui";
import { useEffect, useState } from "react";
import { fetchAccounts, type Account } from "../../../api/accountsClient";
import { deployAutobump } from "./autobumpClient";
import "./autobump.css";

/** Presets rather than a free cron field: these are the intervals anyone actually wants, and a
 * malformed cron would only surface as a task that never fires. The canvas remains the way to get
 * an arbitrary schedule. */
const INTERVALS = [
  { cron: "*/15 * * * *", label: "Каждые 15 минут" },
  { cron: "*/30 * * * *", label: "Каждые 30 минут" },
  { cron: "0 * * * *", label: "Каждый час" },
  { cron: "0 */4 * * *", label: "Каждые 4 часа" },
  { cron: "0 9 * * *", label: "Раз в день, в 9:00" },
] as const;

export interface AutobumpViewProps {
  /** Offered once the flow is deployed, so the operator can go watch it. */
  onDeployed?: () => void;
}

export function AutobumpView({ onDeployed }: AutobumpViewProps) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [cron, setCron] = useState<string>(INTERVALS[1].cron);
  const [maxBumps, setMaxBumps] = useState(20);
  const [reprice, setReprice] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    fetchAccounts()
      .then((all) => setAccounts(all.filter((a) => a.status === "active")))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "не удалось загрузить аккаунты"),
      );
  }, []);

  function toggle(id: string): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleDeploy(): Promise<void> {
    setDeploying(true);
    try {
      await deployAutobump({
        accounts: [...selected],
        scheduleCron: cron,
        maxBumps,
        reprice,
      });
      toast.show("Поднятие включено");
      onDeployed?.();
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось включить", { tone: "danger" });
    } finally {
      setDeploying(false);
    }
  }

  if (error) {
    return (
      <div className="panel-view">
        <Alert tone="danger" title="Не удалось загрузить аккаунты">
          {error}
        </Alert>
      </div>
    );
  }

  if (accounts !== null && accounts.length === 0) {
    return (
      <div className="panel-view">
        <Empty title="Нет активных аккаунтов">
          <p className="panel-empty__hint">
            Поднятие выполняется от имени аккаунта — добавьте хотя бы один на вкладке «Аккаунты».
          </p>
        </Empty>
      </div>
    );
  }

  return (
    <div className="panel-view">
      <div className="panel-view__head">
        <h2 className="panel-view__title">Поднятие</h2>
      </div>

      <div className="autobump">
        <Card className="autobump__section">
          <h3 className="autobump__section-title">Аккаунты</h3>
          <p className="autobump__section-hint">Лоты каждого выбранного аккаунта поднимаются отдельно.</p>
          <div className="autobump__accounts">
            {(accounts ?? []).map((account) => (
              <label key={account.id} className="autobump__account">
                <input
                  type="checkbox"
                  checked={selected.has(account.id)}
                  onChange={() => toggle(account.id)}
                />
                <span className="autobump__account-name">{account.label ?? account.id.slice(0, 8)}</span>
              </label>
            ))}
          </div>
        </Card>

        <Card className="autobump__section">
          <h3 className="autobump__section-title">Как часто</h3>
          <div className="autobump__intervals">
            {INTERVALS.map((option) => (
              <button
                key={option.cron}
                type="button"
                className={
                  cron === option.cron
                    ? "autobump__interval autobump__interval--active"
                    : "autobump__interval"
                }
                onClick={() => setCron(option.cron)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Card>

        <Card className="autobump__section">
          <h3 className="autobump__section-title">Ограничения</h3>
          <div className="autobump__field">
            <label htmlFor="autobump-max">Лотов за один запуск</label>
            {/* A text field, not a numeric one: the spinners and locale-dependent parsing add
                nothing for a plain integer, and a stray scroll over a focused numeric field
                silently changes the value. */}
            <Input
              id="autobump-max"
              type="text"
              inputMode="numeric"
              value={maxBumps}
              onChange={(e) => setMaxBumps(Math.min(1000, Math.max(1, Number(e.target.value.replace(/\D/g, "")) || 1)))}
            />
          </div>
          {/* Stated because the cap is per fire, not a rolling hourly quota — the schedule is what
              paces the bumping, and implying otherwise would promise something the graph does not. */}
          <p className="autobump__section-hint">
            Это ограничение на один запуск. Общий темп задаёт расписание выше.
          </p>
          <label className="autobump__toggle">
            <input type="checkbox" checked={reprice} onChange={() => setReprice((v) => !v)} />
            <span>Обновлять цену вместе с поднятием</span>
          </label>
        </Card>

        <div className="autobump__deploy">
          <div className="autobump__summary">
            <Badge pill tone={selected.size > 0 ? "brand" : "default"}>
              {selected.size > 0 ? `${selected.size} акк.` : "не выбрано"}
            </Badge>
            <span>{INTERVALS.find((i) => i.cron === cron)?.label}</span>
            <span>до {maxBumps} лотов</span>
          </div>
          <Button
            variant="primary"
            loading={deploying}
            disabled={selected.size === 0 || deploying}
            onClick={() => void handleDeploy()}
          >
            Включить поднятие
          </Button>
        </div>
      </div>
    </div>
  );
}
