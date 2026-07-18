import { useEffect, useState } from "react";
import { listComposites, type CatalogNode, type CompositeSummary, type TriggerKind } from "../api/flowClient";
import { useDynamicMethods } from "./hooks/useDynamicMethods";
import { displayLabel } from "./labels";
import "./sidebar.css";
import { useResizablePane } from "../components/ResizablePane";

const TRIGGER_KINDS: TriggerKind[] = ["manual", "schedule", "event"];
const LOGIC_FACADE = "logic";

interface SidebarProps {
  catalog: CatalogNode[] | null;
  catalogError: string | null;
  onAddTrigger: (kind: TriggerKind) => void;
  onAddNode: (entry: CatalogNode) => void;
  /** A composite's internal graph has no trigger, and the backend refuses a nested template
   * (TemplateService._validate_standalone) — so the palette must not offer either. */
  variant?: "flow" | "template";
}

/** Click-to-add palette. No drag-and-drop for the MVP canvas — a click drops the block onto the
 * canvas at a free spot, which is enough to assemble a flow with the mouse.
 *
 * Dynamic methods (e.g. every "market.*" action) are browsed per-facade via useDynamicMethods
 * instead of one flat "Действия" list, so a facade with many methods stays scannable. The
 * `catalog`/`catalogError` props (FlowCanvas's own already-fetched catalog) still gate the
 * overall loading/error banner so the two fetches don't show conflicting states mid-load. */
export function Sidebar({ catalog, catalogError, onAddTrigger, onAddNode, variant = "flow" }: SidebarProps) {
  const { facades, loading, error } = useDynamicMethods();
  const combinedError = catalogError ?? error;
  const stillLoading = !catalog && loading;

  const [composites, setComposites] = useState<CompositeSummary[] | null>(null);
  const [compositeError, setCompositeError] = useState<string | null>(null);

  useEffect(() => {
    listComposites()
      .then(setComposites)
      .catch((err: unknown) =>
        setCompositeError(err instanceof Error ? err.message : "не удалось загрузить составные блоки"),
      );
  }, []);

  // A composite call is a plain node on the canvas — "custom.<template_id>" is the literal
  // NodeSpec.type convention the compiler inlines server-side (app/domain/flow_engine/spec.py).
  // Routing it through the exact same onAddNode the catalog buttons already use means FlowCanvas
  // needs no separate insertion path for composites.
  function addComposite(composite: CompositeSummary): void {
    // A composite is inlined server-side, so it has no catalog entry of its own to copy these
    // from: the schemas are empty and the capabilities unknown until the compiler expands it.
    onAddNode({
      key: `custom.${composite.id}`,
      category: "action",
      input_schema: {},
      output_schema: {},
      idempotent: false,
      capabilities: [],
    });
  }

  const logicEntries = (facades[LOGIC_FACADE] ?? []).filter((entry) => entry.category !== "trigger");
  const actionFacades = Object.entries(facades)
    .filter(([facade]) => facade !== LOGIC_FACADE)
    .map(([facade, entries]) => [facade, entries.filter((entry) => entry.category !== "trigger")] as const)
    .filter(([, entries]) => entries.length > 0);

  const isTemplate = variant === "template";
  const { width, handle } = useResizablePane({ paneId: "palette", defaultWidth: 220, min: 170, max: 420 });

  return (
    <>
    <aside className="sidebar" style={{ width }}>
      {isTemplate ? null : (
        <div className="sidebar__group">
          <h3 className="sidebar__heading">Триггер</h3>
          {TRIGGER_KINDS.map((kind) => (
            <button key={kind} type="button" className="sidebar__item sidebar__item--trigger" onClick={() => onAddTrigger(kind)}>
              <span className="sidebar__dot sidebar__dot--trigger" />
              {displayLabel(kind)}
            </button>
          ))}
        </div>
      )}

      {combinedError ? (
        <p className="sidebar__error">каталог блоков недоступен: {combinedError}</p>
      ) : stillLoading ? (
        <p className="sidebar__loading">загрузка каталога…</p>
      ) : (
        <>
          {actionFacades.map(([facade, entries]) => (
            <div className="sidebar__group" key={facade}>
              <h3 className="sidebar__heading">{displayLabel(facade)}</h3>
              {entries.map((entry) => (
                <button key={entry.key} type="button" className="sidebar__item sidebar__item--action" onClick={() => onAddNode(entry)}>
                  <span className="sidebar__dot sidebar__dot--action" />
                  {displayLabel(entry.key)}
                </button>
              ))}
            </div>
          ))}
          {logicEntries.length > 0 ? (
            <div className="sidebar__group">
              <h3 className="sidebar__heading">Логика</h3>
              {logicEntries.map((entry) => (
                <button key={entry.key} type="button" className="sidebar__item sidebar__item--logic" onClick={() => onAddNode(entry)}>
                  <span className="sidebar__dot sidebar__dot--logic" />
                  {displayLabel(entry.key)}
                </button>
              ))}
            </div>
          ) : null}

          {isTemplate ? null : compositeError ? (
            <p className="sidebar__error">составные блоки недоступны: {compositeError}</p>
          ) : composites && composites.length > 0 ? (
            <div className="sidebar__group">
              <h3 className="sidebar__heading">Составные блоки</h3>
              {composites.map((composite) => (
                <button
                  key={composite.id}
                  type="button"
                  className="sidebar__item sidebar__item--composite"
                  onClick={() => addComposite(composite)}
                >
                  <span className="sidebar__dot sidebar__dot--composite" />
                  {composite.name}
                </button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </aside>
    {handle}
    </>
  );
}
