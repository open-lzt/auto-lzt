import { useEffect, useState } from "react";
import { fetchCatalog, type CatalogNode } from "../../api/flowClient";

interface DynamicMethodsState {
  facades: Record<string, CatalogNode[]>;
  loading: boolean;
  error: string | null;
}

/** A dynamic method's key is "facade.method" (e.g. "market.bump" -> facade "market"); a key with
 * no dot (condition, trigger kinds) buckets under its own category instead so nothing is dropped. */
function groupByFacade(nodes: CatalogNode[]): Record<string, CatalogNode[]> {
  const facades: Record<string, CatalogNode[]> = {};
  for (const node of nodes) {
    const dotIndex = node.key.indexOf(".");
    const facade = dotIndex === -1 ? node.category : node.key.slice(0, dotIndex);
    (facades[facade] ??= []).push(node);
  }
  return facades;
}

export function useDynamicMethods(): DynamicMethodsState {
  const [state, setState] = useState<DynamicMethodsState>({ facades: {}, loading: true, error: null });

  useEffect(() => {
    let cancelled = false;
    fetchCatalog()
      .then((nodes) => {
        if (cancelled) return;
        setState({ facades: groupByFacade(nodes), loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          facades: {},
          loading: false,
          error: err instanceof Error ? err.message : "не удалось загрузить каталог блоков",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
