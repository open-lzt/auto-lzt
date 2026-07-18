import { useCallback, useEffect, useRef, useState } from "react";
import "./resizable-pane.css";

const STORAGE_PREFIX = "lzt-flow.pane-width.";
const KEYBOARD_STEP_PX = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function storedWidth(paneId: string, fallback: number, min: number, max: number): number {
  const raw = localStorage.getItem(STORAGE_PREFIX + paneId);
  const parsed = raw === null ? NaN : Number(raw);
  return Number.isFinite(parsed) ? clamp(parsed, min, max) : fallback;
}

interface ResizablePaneOptions {
  paneId: string;
  defaultWidth: number;
  min: number;
  max: number;
  /** Which edge carries the handle: a left-hand pane grows as the cursor moves right ("right"),
   * a right-hand pane (the inspector) grows as it moves left. */
  edge?: "right" | "left";
}

interface ResizablePaneResult {
  width: number;
  handle: JSX.Element;
}

/** Gives a side pane a draggable edge. The width survives a reload (localStorage, per pane) and
 * the handle is focusable so it can also be nudged with the arrow keys. */
export function useResizablePane({
  paneId,
  defaultWidth,
  min,
  max,
  edge = "right",
}: ResizablePaneOptions): ResizablePaneResult {
  const [width, setWidth] = useState(() => storedWidth(paneId, defaultWidth, min, max));
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef({ pointerX: 0, width: 0 });

  useEffect(() => {
    localStorage.setItem(STORAGE_PREFIX + paneId, String(width));
  }, [paneId, width]);

  // Listening on the window (not the handle) keeps the drag alive when the cursor outruns the
  // 6px handle — otherwise the resize drops the moment you move faster than React re-renders.
  useEffect(() => {
    if (!dragging) return undefined;

    function onMove(event: PointerEvent): void {
      const delta = event.clientX - dragStart.current.pointerX;
      const next = dragStart.current.width + (edge === "right" ? delta : -delta);
      setWidth(clamp(next, min, max));
    }
    function onUp(): void {
      setDragging(false);
    }

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    document.body.classList.add("is-resizing");
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.classList.remove("is-resizing");
    };
  }, [dragging, edge, min, max]);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragStart.current = { pointerX: event.clientX, width };
      setDragging(true);
    },
    [width],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const towardsWider = edge === "right" ? "ArrowRight" : "ArrowLeft";
      const towardsNarrower = edge === "right" ? "ArrowLeft" : "ArrowRight";
      if (event.key === towardsWider) setWidth((w) => clamp(w + KEYBOARD_STEP_PX, min, max));
      else if (event.key === towardsNarrower) setWidth((w) => clamp(w - KEYBOARD_STEP_PX, min, max));
      else return;
      event.preventDefault();
    },
    [edge, min, max],
  );

  const handle = (
    <div
      className={`pane-resizer${dragging ? " pane-resizer--active" : ""}`}
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={width}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-label="Изменить ширину панели"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => setWidth(defaultWidth)}
      title="Потяните, чтобы изменить ширину. Двойной клик — вернуть по умолчанию."
    />
  );

  return { width, handle };
}
