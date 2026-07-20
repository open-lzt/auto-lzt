import { useEffect, useRef, useState } from "react";
import { createTaskStreamToken, taskStreamUrl, type TaskEvent } from "../api/tasksClient";

/** Enough to ride out a redeploy, few enough that a permanently broken stream stops asking. */
const MAX_RECONNECTS = 5;
const RECONNECT_BASE_MS = 1000;

export type StreamState = "connecting" | "live" | "offline";

/**
 * ONE `EventSource` for the whole panel, however many cards are mounted.
 *
 * The card-per-connection alternative is what this exists to avoid: twenty cards would mean twenty
 * sockets and twenty replay buffers for one tenant's events. Here the server pushes every task's
 * lifecycle down one channel and the caller routes each event to the right card.
 *
 * `onEvent` is held in a ref and the connecting effect has an EMPTY dependency list, which is the
 * whole trick. Calling the handler directly would put it — and therefore the task list it closes
 * over — in the deps, so every arriving event would tear the connection down and build a new one:
 * the exact opposite of the property this hook exists to provide, while every mount-time test still
 * passes. If you ever need state in here, reach it through a ref or a functional setState.
 */
export function useTaskStream(onEvent: (event: TaskEvent) => void): StreamState {
  const handlerRef = useRef(onEvent);
  useEffect(() => {
    handlerRef.current = onEvent;
  });

  const [state, setState] = useState<StreamState>("connecting");

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let lastEventId: string | null = null;
    let attempts = 0;

    async function connect(): Promise<void> {
      if (cancelled) return;
      let token: string;
      try {
        token = await createTaskStreamToken();
      } catch {
        // The key is wrong or the server is down. Retrying on a schedule would hammer a dead
        // endpoint forever; the panel shows «offline» and a reload is the recovery.
        setState("offline");
        return;
      }
      if (cancelled) return;

      const base = taskStreamUrl(token);
      const url = lastEventId
        ? `${base}&last_event_id=${encodeURIComponent(lastEventId)}`
        : base;
      const opened = new EventSource(url);
      source = opened;

      opened.onopen = () => {
        // Reset only on a connection that actually opened, so the cap counts consecutive failures
        // rather than lifetime reconnects — a tab left open for a week must still be able to retry.
        attempts = 0;
        setState("live");
      };

      opened.onmessage = (event: MessageEvent<string>) => {
        // Recorded before the handler runs: a throw while applying one event must not cost us the
        // resume point and replay everything after it a second time.
        if (event.lastEventId) lastEventId = event.lastEventId;
        try {
          handlerRef.current(JSON.parse(event.data) as TaskEvent);
        } catch (err) {
          console.error("useTaskStream: malformed event frame", err);
        }
      };

      opened.onerror = () => {
        opened.close();
        if (source === opened) source = null;
        if (cancelled) return;
        if (attempts >= MAX_RECONNECTS) {
          setState("offline");
          return;
        }
        // A token lives about a minute, so the common error here is simply an expired one and the
        // fix is a new token — which is why reconnecting means re-running connect() from the top
        // rather than reopening the same URL.
        attempts += 1;
        setState("connecting");
        timer = setTimeout(() => void connect(), RECONNECT_BASE_MS * attempts);
      };
    }

    void connect();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      source?.close();
    };
  }, []);

  return state;
}
