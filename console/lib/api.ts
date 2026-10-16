// Same-origin calls to the gateway. The session lives in an httpOnly cookie;
// every request carries X-TwinLM-Console, which the gateway requires for
// cookie-authenticated writes (CSRF protection).

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body?.error?.message || body?.detail || res.statusText;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

export async function api<T = unknown>(
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string>; query?: Query } = {},
): Promise<T> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(init.query ?? {})) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  const res = await fetch(path + (qs ? `?${qs}` : ""), {
    method: init.method ?? "GET",
    credentials: "same-origin",
    headers: {
      "X-TwinLM-Console": "1",
      ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const get = <T>(path: string, query?: Query) => api<T>(path, { query });
export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body });
export const del = <T>(path: string, query?: Query) => api<T>(path, { method: "DELETE", query });
