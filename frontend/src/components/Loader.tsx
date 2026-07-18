import "./loader.css";

interface LoaderProps {
  label?: string;
}

/** Centred branded first-paint loader — a rotating accent arc + pulsing core, used while the
 * node catalog is being fetched (never a skeleton bar on first paint). */
export function Loader({ label }: LoaderProps) {
  return (
    <div className="loader" role="status" aria-live="polite">
      <span className="loader__spinner" aria-hidden="true">
        <span className="loader__core" />
      </span>
      {label ? <span className="loader__label">{label}</span> : null}
    </div>
  );
}
