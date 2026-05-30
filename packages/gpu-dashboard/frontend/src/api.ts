// Tiny fetch wrapper — same shape as slurm-mgr's frontend api.ts.

export type ApiError = { error: string; [k: string]: unknown };

// S2.B2 — sub-path-aware API base. See slurm-mgr commit 4c81a32 for
// the full reasoning; this is the gpu-watch mirror.
const API_BASE = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const url = path.startsWith("/") ? `${API_BASE}${path}` : path;
  const res = await fetch(url, init);
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try { data = JSON.parse(text); }
    catch { data = { raw: text }; }
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    if (data && typeof data === "object" && "error" in (data as object)) {
      msg = String((data as ApiError).error);
    }
    const err = new Error(msg) as Error & { detail?: unknown };
    err.detail = data;
    throw err;
  }
  return data as T;
}

export const api = {
  get:    <T = unknown>(p: string) => request<T>("GET", p),
  post:   <T = unknown>(p: string, body?: unknown) => request<T>("POST", p, body),
  delete: <T = unknown>(p: string) => request<T>("DELETE", p),
};

export type NodeSummary = {
  name: string;
  host: string;
  user: string;
  port: number;
  jump_host: string | null;
  dcgm_url: string | null;
  key_present: boolean;
};
