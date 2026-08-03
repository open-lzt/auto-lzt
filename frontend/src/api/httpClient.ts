// The one HTTP surface every client in src/api and src/panel goes through. It exists so there is a
// single place that decides what an ErrorEnvelope is and a single way an error reaches the UI — a
// second fetch helper would mean two of both.
//
// Envelope shape: app/core/errors.py (ErrorEnvelope).

interface ErrorEnvelope {
  code: string;
  message: string;
  request_id: string;
}

/** Raised on a non-2xx backend response; carries the node the compiler blamed, if any (400 from
 * CompileError), so the caller can highlight the offending node instead of a bare toast. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly nodeId: string | null;

  constructor(status: number, envelope: ErrorEnvelope, nodeId: string | null) {
    super(envelope.message);
    this.status = status;
    this.code = envelope.code;
    this.nodeId = nodeId;
  }
}

// A compile error's node id isn't in the error envelope (server logs it, doesn't echo it back) —
// the message text embeds "(node <id>)" per CompileError.__str__; parse it back out here so the
// canvas can still highlight the right node without a backend contract change.
function extractNodeId(message: string): string | null {
  const match = /\(node ([^)]+)\)/.exec(message);
  return match ? match[1] : null;
}

export const API_KEY_STORAGE_KEY = "lzt-flow.api-key";

// Restored eagerly at module load so a page refresh keeps the operator authenticated without a
// re-prompt; sessionStorage (not localStorage) so the key doesn't outlive the browser tab.
let apiKey: string | null = sessionStorage.getItem(API_KEY_STORAGE_KEY);

export function setApiKey(key: string): void {
  apiKey = key;
  sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function getApiKey(): string | null {
  return apiKey;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  const resp = await fetch(`/api${path}`, {
    ...init,
    headers: { ...headers, ...init?.headers },
  });
  if (!resp.ok) {
    // A 502/504 from a gateway (or any infra failure before the app handler runs) responds with
    // an HTML error page, not the JSON envelope — awaiting resp.json() there throws "Unexpected
    // token <" and hides the real HTTP status behind a parse error.
    const contentType = resp.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      throw new ApiError(
        resp.status,
        { code: "ERR-HTTP", message: `HTTP ${resp.status} — сервис недоступен`, request_id: "" },
        null,
      );
    }
    const envelope = (await resp.json()) as ErrorEnvelope;
    throw new ApiError(resp.status, envelope, extractNodeId(envelope.message));
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

/** Builds an ApiError for a failure the client itself detects (a schema version it cannot render,
 * for example). Kept here so such errors carry the same shape as a server-sent one. */
export function clientError(message: string): ApiError {
  return new ApiError(200, { code: "ERR-HTTP", message, request_id: "" }, null);
}
