import { useEffect, useRef, useState } from "react";
import "./countdown.css";

const SECOND = 1000;
const URGENT_S = 60;

export interface CountdownProps {
  targetAt: string | null;
  serverTime: string;
  className?: string;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** `2 ч 05 мин` · `43 мин 05 с` · `05 с` — a suffix on every unit, never a bare mm:ss colon. */
function format(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} ч ${pad(minutes)} мин`;
  if (minutes > 0) return `${minutes} мин ${pad(seconds)} с`;
  return `${pad(seconds)} с`;
}

/** Anchored on the server's clock, not the browser's — a skewed local clock would silently fire
 * every task early/late. Renders "сейчас" instead of a negative number once the target passes. */
export function Countdown({ targetAt, serverTime, className }: CountdownProps) {
  // Skew measured once at mount: re-deriving it per tick would jump the display on clock adjustment.
  const skewRef = useRef(new Date(serverTime).getTime() - Date.now());
  const [now, setNow] = useState(() => Date.now() + skewRef.current);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() + skewRef.current), SECOND);
    return () => window.clearInterval(id);
  }, []);

  if (targetAt === null) {
    return (
      <span className={["countdown", "countdown--idle", className].filter(Boolean).join(" ")}>
        —
      </span>
    );
  }

  const remainingMs = new Date(targetAt).getTime() - now;
  const remainingS = Math.max(0, Math.floor(remainingMs / SECOND));
  const urgent = remainingS <= URGENT_S;

  return (
    <span
      className={["countdown", urgent ? "countdown--urgent" : "", className].filter(Boolean).join(" ")}
      aria-live="off" // ticks every second — announcing each tick would make a screen reader unusable
      title={new Date(targetAt).toLocaleString("ru-RU")}
    >
      {remainingS === 0 ? "сейчас" : format(remainingS)}
    </span>
  );
}
