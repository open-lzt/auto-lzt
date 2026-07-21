import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { authRequired, fetchFlows, getApiKey, setApiKey } from "../api/flowClient";
import "./auth-gate.css";

interface AuthGateProps {
  children: ReactNode;
}

type GateStatus = "checking" | "open" | "authed" | "prompting" | "validating";

/** Gates the app behind an API key — but only when there is one.
 *
 * The server's `require_api_key` is a NO-OP when no key is configured, which is the self-host
 * default. A prompt shown anyway would be a painted lock: every string typed into it "worked",
 * because the read used to validate it succeeds for everybody. Someone setting up a stand would
 * see a login screen, type a key, and reasonably conclude they were protected.
 *
 * So the gate ASKS first (`GET /auth/required`). No key configured → render the app with a
 * standing warning that says so out loud, rather than a login that means nothing.
 *
 * A key already in sessionStorage renders children immediately: no re-validation per reload, the
 * backend rejects a stale key on the first real call and that re-opens this gate.
 */
export function AuthGate({ children }: AuthGateProps) {
  const [status, setStatus] = useState<GateStatus>("checking");
  const [keyInput, setKeyInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authRequired()
      .then(({ required }) => {
        if (cancelled) return;
        if (!required) setStatus("open");
        else setStatus(getApiKey() ? "authed" : "prompting");
      })
      .catch(() => {
        // Unreachable or erroring backend: prompt rather than open. Failing closed here costs a
        // needless login screen; failing open would hide an unprotected stand behind a guess.
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

  if (status === "checking") {
    return null;
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
