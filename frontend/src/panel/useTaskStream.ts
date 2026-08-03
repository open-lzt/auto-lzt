import { useEffect, useRef, useState } from "react";
import { createTaskStreamToken, taskStreamUrl, type TaskEvent } from "../api/tasksClient";

const MAX_RECONNECTS = 5;
const RECONNECT_BASE_MS = 1000;

export type StreamState = "connecting" | "live" | "offline";

/** ONE `EventSource` for the whole panel. `onEvent` is held in a ref so the connecting effect can
 * keep an empty dependency list — calling it directly would tear down and rebuild the connection
 * on every event. Need state in here? Route it through a ref or functional setState. */
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
        // Reset only on an opened connection, so the cap counts consecutive failures, not lifetime ones.
        attempts = 0;
        setState("live");
      };

      opened.onmessage = (event: MessageEvent<string>) => {
        // Recorded before the handler runs — a throw there must not cost the resume point.
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
        // Reconnect re-runs connect() from the top (fetches a fresh token) rather than reopening the URL.
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
