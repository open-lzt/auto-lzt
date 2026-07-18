import type { CSSProperties, ReactNode } from "react";
import { Tooltip } from "../ui/Tooltip";
import "./node-styles.css";

// One line-icon set, currentColor, reused across Trigger/Action/Logic — no icon library, three
// tiny inline SVGs are cheaper than a dependency for a 3-category canvas.
export function ZapIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" />
    </svg>
  );
}

export function BoltIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2v6M12 16v6M4.9 4.9l4.2 4.2M14.9 14.9l4.2 4.2M2 12h6M16 12h6M4.9 19.1l4.2-4.2M14.9 9.1l4.2-4.2" />
    </svg>
  );
}

export function BranchIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="12" r="2.5" />
      <path d="M8.2 7.2 15.8 11M8.2 16.8 15.8 13" />
    </svg>
  );
}

const CATEGORY_ICON: Record<string, ReactNode> = {
  trigger: <ZapIcon />,
  action: <BoltIcon />,
  logic: <BranchIcon />,
};

const CATEGORY_LABEL: Record<string, string> = {
  trigger: "триггер",
  action: "действие",
  logic: "логика",
};

interface NodeShellProps {
  category: "trigger" | "action" | "logic";
  label: string;
  description: string;
  selected?: boolean;
  errorMessage?: string;
  children?: ReactNode;
}

/** Shared chrome for all three node categories: icon + category label + title, colored by
 * `--node-accent` (set per category via inline style, see TriggerNode/ActionNode/LogicNode).
 * Wrapped in a hover Tooltip carrying the node type's description. */
export function NodeShell({ category, label, description, selected, errorMessage, children }: NodeShellProps) {
  const classes = ["flow-node", selected && "selected", errorMessage && "has-error"]
    .filter(Boolean)
    .join(" ");
  const style = {
    "--node-accent": `var(--cat-${category})`,
    "--node-accent-dim": `var(--cat-${category}-dim)`,
  } as CSSProperties;
  return (
    <Tooltip label={description}>
      <div className={classes} style={style}>
        <div className="flow-node__head">
          <span className="flow-node__icon">{CATEGORY_ICON[category]}</span>
          <div className="flow-node__titles">
            <span className="flow-node__category">{CATEGORY_LABEL[category]}</span>
            <span className="flow-node__label">{label}</span>
          </div>
        </div>
        {children ? <div className="flow-node__body">{children}</div> : null}
        {errorMessage ? <div className="flow-node__error">{errorMessage}</div> : null}
      </div>
    </Tooltip>
  );
}
