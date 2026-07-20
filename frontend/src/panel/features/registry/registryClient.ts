import { request } from "../../../api/flowClient";

export interface ModuleRef {
  name: string;
  version: string;
  /** Integrity of the transfer — the module you install is byte-for-byte the one that was
   * reviewed. It is NOT a signature: it says nothing about who wrote it. */
  sha256: string;
}

export interface ModuleImported {
  flow_id: string;
  name: string;
}

/** The official registry's modules.
 *
 * EMPTY when the registry is unreachable, never stale — the client is fail-closed by design and
 * this call does not soften that. The screen has to say "недоступен" rather than "пусто", because
 * the two look identical here and mean opposite things.
 */
export function fetchOfficialModules(): Promise<ModuleRef[]> {
  return request<ModuleRef[]>("/modules/official");
}

/** Install a module as a flow of this tenant. Re-validated server-side against THIS process's
 * node registry — the registry's own CI is not taken on trust. */
export function importModule(name: string): Promise<ModuleImported> {
  return request<ModuleImported>("/modules/import", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}
