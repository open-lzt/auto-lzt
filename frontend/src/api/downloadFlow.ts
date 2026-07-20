import { exportFlow, type FlowSpec } from "./flowClient";

/** Save a flow spec to the operator's disk as pretty-printed JSON.
 *
 * An object URL rather than a `data:` link: a large graph exceeds the URL length limits some
 * browsers still enforce on `data:`, and the blob is revoked immediately after the click.
 */
export function downloadFlowSpec(spec: FlowSpec, fileName: string): void {
  const blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

/** Fetch a saved flow and save it to disk under a filesystem-safe name. */
export async function downloadFlowById(flowId: string, name: string): Promise<void> {
  const spec = await exportFlow(flowId);
  const safe = name.replace(/[^\w\-.]+/g, "_").slice(0, 60) || "flow";
  downloadFlowSpec(spec, `${safe}.json`);
}
