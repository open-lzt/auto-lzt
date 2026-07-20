import { request } from "../../../api/flowClient";

export interface AutobumpSettings {
  accounts: string[];
  scheduleCron: string;
  maxBumps: number;
  reprice: boolean;
}

export interface AutobumpDeployed {
  flow_id: string;
  trigger_id: string;
}

/** Deploys the settings as an ordinary flow with a schedule attached. The result is editable on the
 * canvas — the form is one way to author a graph, not a separate runtime. */
export async function deployAutobump(settings: AutobumpSettings): Promise<AutobumpDeployed> {
  return request<AutobumpDeployed>("/panel/presets/autobump", {
    method: "POST",
    body: JSON.stringify({
      accounts: settings.accounts,
      schedule_cron: settings.scheduleCron,
      max_bumps: settings.maxBumps,
      reprice: settings.reprice,
    }),
  });
}
