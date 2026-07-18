import type { ReactNode } from "react";

interface TooltipProps {
  label: string;
  children: ReactNode;
}

/** CSS-only hover tooltip: `.tooltip` is the `position:relative` hover/focus target, `.tooltip__bubble`
 * is absolutely positioned above it and revealed via `:hover`/`:focus-within` (styles in flow-canvas.css). */
export function Tooltip({ label, children }: TooltipProps) {
  return (
    <div className="tooltip">
      {children}
      <span className="tooltip__bubble" role="tooltip">
        {label}
      </span>
    </div>
  );
}
