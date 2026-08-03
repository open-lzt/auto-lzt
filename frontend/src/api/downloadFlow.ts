import { exportFlow, type FlowSpec } from "./flowClient";

// Object URL, not `data:` — a large graph exceeds the browser's data-URI length limit.
export function downloadFlowSpec(spec: FlowSpec, fileName: string): void {
  const blob = new Blob([JSON.stringify(spec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

export async function downloadFlowById(flowId: string, name: string): Promise<void> {
  const spec = await exportFlow(flowId);
  const safe = name.replace(/[^\w\-.]+/g, "_").slice(0, 60) || "flow";
  downloadFlowSpec(spec, `${safe}.json`);
}
