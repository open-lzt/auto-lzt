import { useEffect, useRef, useState } from "react";
import { fetchFlowStatus, type FlowStatusResponse } from "../api/flowClient";
import "./live-badge.css";

const POLL_INTERVAL_MS = 5000;

interface LiveBadgeProps {
  flowId: string | null;
}

/** Polls GET /flows/{id}/status every 5s (wave-06 §Logic: polling, not a socket — cheap enough at
 * this scale) and renders "running 24/7 · N аккаунтов" once the flow has a live run history. */
export function LiveBadge({ flowId }: LiveBadgeProps) {
  const [status, setStatus] = useState<FlowStatusResponse | null>(null);
  const timerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    setStatus(null);
    if (!flowId) return;

    let cancelled = false;
    const poll = () => {
      fetchFlowStatus(flowId)
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch(() => {
          /* transient poll failure — keep showing the last known status, try again next tick */
        });
    };
    poll();
    timerRef.current = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timerRef.current);
    };
  }, [flowId]);

  if (!flowId || !status) return null;

  return (
    <div className={`live-badge ${status.running ? "live-badge--live" : ""}`}>
      <span className="live-badge__dot" />
      {status.running ? `running 24/7 · ${status.active_accounts} аккаунтов` : "ожидает первого запуска"}
    </div>
  );
}
