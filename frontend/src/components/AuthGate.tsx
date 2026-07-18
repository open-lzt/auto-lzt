import { useState, type FormEvent, type ReactNode } from "react";
import { fetchFlows, getApiKey, setApiKey } from "../api/flowClient";
import "./auth-gate.css";

interface AuthGateProps {
  children: ReactNode;
}

type GateStatus = "checking" | "authed" | "prompting" | "validating";

/** Gates the whole app behind an API key. A key already in sessionStorage renders `children`
 * immediately (no re-validation on every reload — the backend rejects it on the first real call
 * if it's gone stale, which then re-opens this gate). A freshly entered key is validated with a
 * cheap read before trusting it, and cleared on failure so a bad key can't loop-fail forever. */
export function AuthGate({ children }: AuthGateProps) {
  const [status, setStatus] = useState<GateStatus>(getApiKey() ? "authed" : "prompting");
  const [keyInput, setKeyInput] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="auth-gate">
      <form className="auth-gate__card" onSubmit={(e) => void handleSubmit(e)}>
        <h2 className="auth-gate__title">Доступ к lzt-flow</h2>
        <p className="auth-gate__subtitle">Введите API-ключ, чтобы продолжить</p>
        <input
          className="auth-gate__input"
          type="password"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder="API-ключ"
          autoFocus
          disabled={status === "validating"}
        />
        {error ? <p className="auth-gate__error">{error}</p> : null}
        <button type="submit" className="auth-gate__submit" disabled={status === "validating"}>
          {status === "validating" ? "Проверяем…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
