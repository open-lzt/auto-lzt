import { useCallback, useEffect, useState } from "react";
import { listComposites, type CompositeSummary } from "../api/flowClient";
import { useResizablePane } from "./ResizablePane";
import "./composite-list.css";

interface CompositeListProps {
  activeCompositeId: string | null;
  onSelect: (id: string) => void;
  onCreateNew: () => void;
  /** Bumped by the parent after a composite is saved — the list is fetched on mount and stays
   * mounted while the editor works beside it, so a newly saved block never showed up. */
  reloadToken?: number;
}

/** Sidebar panel listing every saved composite template so the operator can open one for
 * editing in AuthoringMode or start a fresh one. Mirrors FlowList's fetch-on-mount /
 * refetch-after-mutation shape, kept as its own component (not a second entity type crammed
 * into FlowList) since composites are a template surface, not a flow. */
export function CompositeList({ activeCompositeId, onSelect, onCreateNew, reloadToken = 0 }: CompositeListProps) {
  const [composites, setComposites] = useState<CompositeSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { width, handle } = useResizablePane({ paneId: "composite-list", defaultWidth: 200, min: 160, max: 420 });

  const reload = useCallback(() => {
    listComposites()
      .then((list) => {
        setComposites(list);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "не удалось загрузить составные блоки"),
      );
  }, []);

  useEffect(() => {
    reload();
  }, [reload, reloadToken]);

  return (
    <>
    <aside className="composite-list" style={{ width }}>
      <div className="composite-list__header">
        <h3 className="composite-list__heading">Составные блоки</h3>
        <button type="button" className="composite-list__new" onClick={onCreateNew}>
          + новый
        </button>
      </div>

      {error ? <p className="composite-list__error">{error}</p> : null}

      {!composites ? (
        <p className="composite-list__loading">загрузка…</p>
      ) : composites.length === 0 ? (
        <p className="composite-list__empty">пока нет ни одного составного блока — нажмите «+ новый»</p>
      ) : (
        <ul className="composite-list__items">
          {composites.map((composite) => (
            <li
              key={composite.id}
              className={`composite-list__item${composite.id === activeCompositeId ? " composite-list__item--active" : ""}`}
            >
              <button type="button" className="composite-list__name" onClick={() => onSelect(composite.id)}>
                {composite.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
    {handle}
    </>
  );
}
