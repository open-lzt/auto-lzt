import { request } from "../../../api/httpClient";

export interface ModuleRef {
  name: string;
  version: string;
  /** Integrity of the transfer, not a signature — says nothing about who wrote it. */
  sha256: string;
}

export interface ModuleImported {
  flow_id: string;
  name: string;
}

/** Fail-closed by design: returns [] when the registry is unreachable, never stale data. */
export function fetchOfficialModules(): Promise<ModuleRef[]> {
  return request<ModuleRef[]>("/modules/official");
}

export function importModule(name: string): Promise<ModuleImported> {
  return request<ModuleImported>("/modules/import", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}
