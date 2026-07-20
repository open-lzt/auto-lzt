import { Alert, Badge, Button, Card, Empty, Icon, useToast } from "@open-lzt/ui";
import { useCallback, useEffect, useState } from "react";
import { downloadFlowById } from "../../../api/downloadFlow";
import { fetchOfficialModules, importModule, type ModuleRef } from "./registryClient";
import "./registry.css";

/** The official flow registry: install a reviewed flow, or take it away as JSON.
 *
 * A list, not a form, so it needs no declarative surface — there is nothing here for an operator
 * to fill in.
 */
export function RegistryView() {
  const [modules, setModules] = useState<ModuleRef[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // flow ids of what this session installed, so «Скачать» can appear on the row it came from.
  const [installed, setInstalled] = useState<Record<string, string>>({});
  const toast = useToast();

  const reload = useCallback(async () => {
    try {
      setModules(await fetchOfficialModules());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "реестр недоступен");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function handleInstall(module: ModuleRef): Promise<void> {
    setBusy(module.name);
    try {
      const result = await importModule(module.name);
      setInstalled((prev) => ({ ...prev, [module.name]: result.flow_id }));
      toast.show(`«${result.name}» установлен — он появился во «Флоу»`);
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось установить", { tone: "danger" });
    } finally {
      setBusy(null);
    }
  }

  async function handleDownload(module: ModuleRef, flowId: string): Promise<void> {
    setBusy(module.name);
    try {
      await downloadFlowById(flowId, module.name);
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "не удалось скачать", { tone: "danger" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel-view">
      <div className="panel-view__head">
        <h2 className="panel-view__title">Реестр</h2>
      </div>

      <p className="registry__intro">
        Готовые флоу, отревьюенные мейнтейнером.
        <br />
        Модуль — это <b>данные</b>, а не код: установленный флоу можно открыть и прочитать целиком.
      </p>

      {error ? (
        <Alert tone="danger" title="Реестр недоступен">
          {error}
        </Alert>
      ) : null}

      {/* An unreachable registry and an empty one look identical over the wire — the client is
          fail-closed and returns []. Saying which is which is the whole job of this state. */}
      {modules !== null && modules.length === 0 && !error ? (
        <Empty title="Реестр пуст или недоступен">
          <p className="panel-empty__hint">
            Список приходит пустым и когда в реестре ничего нет, и когда до него не достучались.
            <br />
            Проверьте сеть и обновите страницу.
          </p>
        </Empty>
      ) : null}

      {modules !== null && modules.length > 0 ? (
        <div className="registry__list">
          {modules.map((module) => {
            const flowId = installed[module.name];
            return (
              <Card key={module.name} className="registry__row">
                <span className="registry__name">{module.name}</span>
                <Badge pill>{module.version}</Badge>
                {/* Integrity of the transfer, not a signature — it says nothing about the author.
                    Shown truncated because the point is comparability, not reading it. */}
                <code className="registry__hash" title={`sha256: ${module.sha256}`}>
                  {module.sha256.slice(0, 12)}
                </code>
                <div className="registry__actions">
                  {flowId ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={busy === module.name}
                      onClick={() => void handleDownload(module, flowId)}
                    >
                      <Icon name="download" size={14} />
                      Скачать
                    </Button>
                  ) : null}
                  <Button
                    variant={flowId ? "ghost" : "primary"}
                    size="sm"
                    loading={busy === module.name}
                    onClick={() => void handleInstall(module)}
                  >
                    {flowId ? "Установить ещё раз" : "Установить"}
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
