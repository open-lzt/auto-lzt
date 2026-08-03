import { request } from "../api/httpClient";

export interface PanelTab {
  key: string;
  title: string;
  order: number;
  icon: string | null;
  origin: string;
}

export async function fetchPanelTabs(): Promise<PanelTab[]> {
  return request<PanelTab[]>("/panel/tabs");
}
