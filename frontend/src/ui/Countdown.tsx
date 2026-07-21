import { useEffect, useRef, useState } from "react";
import "./countdown.css";

const SECOND = 1000;
/** Below this the countdown turns accent — the point where "soon" becomes "now-ish". */
const URGENT_S = 60;

export interface CountdownProps {
  /** ISO instant the task next fires, or null when it is paused / has no schedule. */
  targetAt: string | null;
  /** ISO instant the SERVER produced this page at. See the skew note below. */
  serverTime: string;
  className?: string;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Two most significant units, each carrying its own suffix: `2 ч 05 мин` · `43 мин 05 с` · `05 с`.
 *
 * The previous shape mixed a suffixed unit with a bare clock — `1ч 43:05` on one card, `43:05` on
 * the next — so the same field could be read as minutes or as hours depending on which card you
 * looked at. A colon only means mm:ss if you already know the scale; a suffix always says it.
 * Seconds are dropped past the hour: a countdown that far out does not need them ticking. */
function format(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} ч ${pad(minutes)} мин`;
  if (minutes > 0) return `${minutes} мин ${pad(seconds)} с`;
  return `${pad(seconds)} с`;
}

/**
 * The hero's focal element: a live countdown to a task's next run.
 *
 * Anchored on the SERVER's clock, not the browser's. Every card would otherwise be off by the
 * viewer's clock skew — which is silent, plausible-looking, and wrong: a machine five minutes fast
 * shows every task firing five minutes early, and nothing about the display says so. The offset is
 * measured once at mount and applied to every subsequent tick.
 *
 * Renders "сейчас" rather than a negative number once the target passes. A task whose moment has
 * arrived but whose worker has not yet picked it up is the normal case for a second or two, and
 * `-00:03` reads as a bug.
 */
export function Countdown({ targetAt, serverTime, className }: CountdownProps) {
  // Measured once: re-deriving it per tick would let a mid-session clock adjustment jump the
  // display, which is exactly the jitter anchoring exists to prevent.
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
      // The value changes every second; announcing each tick would make a screen reader unusable.
      aria-live="off"
      title={new Date(targetAt).toLocaleString("ru-RU")}
    >
      {remainingS === 0 ? "сейчас" : format(remainingS)}
    </span>
  );
}
