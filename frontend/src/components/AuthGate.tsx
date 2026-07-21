import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { authRequired, fetchFlows, getApiKey, setApiKey } from "../api/flowClient";
import "./auth-gate.css";

interface AuthGateProps {
  children: ReactNode;
}

type GateStatus = "checking" | "open" | "shut" | "authed" | "prompting" | "validating";

/** Gates the app behind an API key — reflecting what the SERVER actually does, in all three cases.
 *
 * The gate ASKS first (`GET /auth/required`) instead of guessing, because a prompt shown against a
 * server that accepts everyone is a painted lock: every string typed into it "works", and whoever
 * set the stand up concludes they are protected.
 *
 * The subtlety that made an earlier version of this file wrong: `require_api_key` fails CLOSED, so
 * "no key configured" is not one situation but two, and `required` alone cannot separate them.
 *
 *   required=true              → a real key is demanded          → prompt
 *   required=false, open=true  → the hatch is on, anyone is in   → render + say it is unprotected
 *   required=false, open=false → the server 401s every request   → say THAT; rendering is a lie
 *
 * The third is the stock self-host default. This component used to treat it as the second and
 * render a dashboard where every call fails, under a banner announcing the panel was open to
 * anyone — claiming to be unprotected while in fact refusing everything.
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
      .then(({ required, open }) => {
        if (cancelled) return;
        if (required) setStatus(getApiKey() ? "authed" : "prompting");
        else if (open) setStatus("open");
        // Neither: no key to satisfy it and no hatch — every protected call will 401. Prompting
        // would be cruel (no key exists that works) and rendering would be false.
        else setStatus("shut");
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

  // Rendering the panel here would be a lie: the server refuses every protected request and no key
  // the operator can type will change that. Say what to set instead of showing a dashboard whose
  // every button fails.
  if (status === "shut") {
    return (
      <div className="auth-gate">
        <div className="auth-gate__card" role="alert">
          <h2 className="auth-gate__title">Сервер отклоняет все запросы</h2>
          <p className="auth-gate__subtitle">
            Ключ не задан, и вход без ключа не разрешён — панель работать не сможет.
          </p>
          <p className="auth-gate__subtitle">
            Задайте <code>LZT_FLOW_API_KEY</code> и перезапустите сервер. Для локальной отладки без
            ключа — <code>LZT_FLOW_ALLOW_UNAUTHENTICATED=1</code>.
          </p>
        </div>
      </div>
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
