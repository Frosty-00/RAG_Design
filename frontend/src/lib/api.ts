/** Lightweight fetch wrapper with bearer auth + JSON helpers.
 *  401 → wipes the stored token and surfaces a typed error so React Query
 *  retries can be disabled at the call site.
 */
import { clearToken, getToken } from "@/lib/auth";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string,
              public readonly body?: unknown) {
    super(message);
  }
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function handle<T>(resp: Response): Promise<T> {
  if (resp.status === 401) {
    clearToken();
    throw new ApiError(401, "unauthorized");
  }
  if (!resp.ok) {
    let body: unknown = undefined;
    try { body = await resp.json(); } catch { /* not json */ }
    throw new ApiError(resp.status, `${resp.status} ${resp.statusText}`, body);
  }
  if (resp.status === 204) return undefined as T;
  const ct = resp.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return resp.json() as Promise<T>;
  return (await resp.text()) as unknown as T;
}

export const api = {
  get<T>(path: string): Promise<T> {
    return fetch(path, { headers: { ...authHeaders() } }).then(handle<T>);
  },
  post<T>(path: string, body?: unknown): Promise<T> {
    return fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }).then(handle<T>);
  },
  del<T>(path: string): Promise<T> {
    return fetch(path, {
      method: "DELETE",
      headers: { ...authHeaders() },
    }).then(handle<T>);
  },
  upload<T>(path: string, formData: FormData): Promise<T> {
    return fetch(path, {
      method: "POST",
      headers: { ...authHeaders() },  // do NOT set content-type; browser fills boundary
      body: formData,
    }).then(handle<T>);
  },
};

/** Raw streaming POST — returns the Response for SSE consumers. */
export async function streamPost(path: string, body: unknown): Promise<Response> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders(), accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (resp.status === 401) {
    clearToken();
    throw new ApiError(401, "unauthorized");
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, `${resp.status} ${resp.statusText}`);
  }
  return resp;
}
