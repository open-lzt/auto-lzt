import { Alert, Button, Icon, Input, Skeleton } from "@open-lzt/ui";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { authRequired, fetchFlows } from "../api/flowClient";
import { getApiKey, setApiKey } from "../api/httpClient";
import "./auth-gate.css";

interface AuthGateProps {
  children: ReactNode;
}

type GateStatus = "checking" | "open" | "shut" | "authed" | "prompting" | "validating";

/** Asks GET /auth/required first rather than guessing — `required` alone can't tell open from shut:
 *
 *   required=true              → real key demanded       → prompt
 *   required=false, open=true  → anyone is let in         → render + say unprotected
 *   required=false, open=false → server 401s everything   → say that; rendering would lie
 *
 * A key in sessionStorage renders immediately; a stale key just gets rejected by the first real call.
 */
export function AuthGate({ children }: AuthGateProps) {
  const [status, setStatus] = useState<GateStatus>("checking");
  const [keyInput, setKeyInput] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authRequired()
      .then(({ required, open }) => {
        if (cancelled) return;
        if (required) setStatus(getApiKey() ? "authed" : "prompting");
        else if (open) setStatus("open");
        else setStatus("shut"); // no key would work and no hatch — every call will 401
      })
      .catch(() => {
        // Fail closed on an unreachable backend: failing open would hide an unprotected stand.
        if (!cancelled) setStatus(getApiKey() ? "authed" : "prompting");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) {
      setError("введите API-ключ");
      return;
    }
    setStatus("validating");
    setError(null);
    setApiKey(key);
    try {
      await fetchFlows();
      setStatus("authed");
    } catch (err) {
      setApiKey(""); // clears the persisted key too (setApiKey always writes-through to sessionStorage)
      setError(err instanceof Error ? err.message : "неверный API-ключ");
      setStatus("prompting");
    }
  }

  if (status === "authed") {
    return <>{children}</>;
  }

  if (status === "open") {
    return (
      <>
        <div className="auth-gate__open-banner" role="status">
          Ключ не задан — панель открыта всем, кто дотянется до этого адреса. Задайте
          <code> LZT_FLOW_API_KEY</code>, прежде чем выставлять её наружу.
        </div>
        {children}
      </>
    );
  }

  if (status === "checking") {
    return (
      <div className="auth-gate">
        <div className="auth-gate__card" aria-busy="true">
          <div className="auth-gate__head">
            <Skeleton className="auth-gate__skeleton-title" />
            <Skeleton className="auth-gate__skeleton-subtitle" />
          </div>
          <Skeleton className="auth-gate__skeleton-label" />
          <Skeleton className="auth-gate__skeleton-field" />
          <Skeleton className="auth-gate__skeleton-button" />
        </div>
      </div>
    );
  }

  if (status === "shut") {
    return (
      <div className="auth-gate">
        <div className="auth-gate__card">
          <Alert tone="danger" title="Сервер отклоняет все запросы">
            Ключ не задан, и вход без ключа не разрешён — панель работать не сможет.
          </Alert>
          <p className="auth-gate__hint">
            Задайте <code>LZT_FLOW_API_KEY</code> и перезапустите сервер. Для локальной отладки без
            ключа — <code>LZT_FLOW_ALLOW_UNAUTHENTICATED=1</code>.
          </p>
        </div>
      </div>
    );
  }

  const busy = status === "validating";

  return (
    <div className="auth-gate">
      <form className="auth-gate__card" onSubmit={(e) => void handleSubmit(e)}>
        <div className="auth-gate__head">
          <h1 className="auth-gate__title">Панель автоматизации</h1>
          <p className="auth-gate__subtitle">
            Введите ключ доступа — тот, что задан в <code>LZT_FLOW_API_KEY</code> на сервере.
          </p>
        </div>

        <label className="auth-gate__label" htmlFor="auth-gate-key">
          Ключ доступа
        </label>
        <div className="auth-gate__field">
          <Input
            id="auth-gate-key"
            type={keyVisible ? "text" : "password"}
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="Вставьте ключ"
            autoComplete="off"
            autoFocus
            invalid={error !== null}
            disabled={busy}
          />
          <button
            type="button"
            className="auth-gate__reveal"
            aria-pressed={keyVisible}
            aria-label={keyVisible ? "Скрыть ключ" : "Показать ключ"}
            title={keyVisible ? "Скрыть ключ" : "Показать ключ"}
            onClick={() => setKeyVisible((v) => !v)}
          >
            <Icon name="eye" size={14} />
          </button>
        </div>

        {error ? (
          <p className="auth-gate__error" role="alert">
            {error}
          </p>
        ) : null}

        <Button type="submit" variant="primary" block loading={busy} disabled={busy}>
          Войти
        </Button>
      </form>
    </div>
  );
}
