// Envelope shape mirrors app/core/errors.py (ErrorEnvelope).

interface ErrorEnvelope {
  code: string;
  message: string;
  request_id: string;
}

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

// Node id isn't in the error envelope; the message embeds "(node <id>)" per CompileError.__str__.
function extractNodeId(message: string): string | null {
  const match = /\(node ([^)]+)\)/.exec(message);
  return match ? match[1] : null;
}

export const API_KEY_STORAGE_KEY = "lzt-flow.api-key";

// sessionStorage, not localStorage — the key must not outlive the browser tab.
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
    // A gateway failure (502/504) returns an HTML page, not the JSON envelope — resp.json() would
    // throw a misleading parse error and hide the real HTTP status.
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

export function clientError(message: string): ApiError {
  return new ApiError(200, { code: "ERR-HTTP", message, request_id: "" }, null);
}
