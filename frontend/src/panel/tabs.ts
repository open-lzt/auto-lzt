import { request } from "../api/flowClient";

export interface PanelTab {
  key: string;
  title: string;
  order: number;
  icon: string | null;
  /** Which package contributed the tab — "builtin" for the host. */
  origin: string;
}

/** The tab strip is a backend fact. Adding a tab is a plugin shipping one, not a frontend edit —
 * which is why this is fetched rather than declared as a literal array here. */
export async function fetchPanelTabs(): Promise<PanelTab[]> {
  return request<PanelTab[]>("/panel/tabs");
}
