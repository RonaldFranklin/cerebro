export type Ceiling = "public" | "internal" | "restricted";
export interface Session {
  ceiling: Ceiling;
  csrf_token: string;
  absolute_remaining_seconds: number;
}
export interface Overview {
  core: { id: string; title: string; summary: string; body: string }[];
  core_words: number;
  core_truncated: boolean;
  areas: string[];
  hubs: { id: string; title: string; summary: string }[];
}

export interface ValidationReport {
  ok: boolean;
  violations: {code: string; field: string | null; severity: string; line: number | null}[];
}
export interface WriteWarning {code: string; message: string}

export class ApiError extends Error {
  constructor(readonly status: number, readonly validation?: ValidationReport) {
    super("The request could not be completed.");
  }
}

async function request<T>(path: string, options: RequestInit): Promise<T> {
  const url = new URL(path, location.origin);
  if (!path.startsWith("/api/") || url.origin !== location.origin || !url.pathname.startsWith("/api/") || url.hash) {
    throw new Error("Invalid API path.");
  }
  const response = await fetch(path, {
    ...options, credentials: "same-origin", mode: "same-origin", cache: "no-store", redirect: "error",
  });
  if (!response.ok) {
    if (response.status === 422 && (options.method === "PUT" || options.method === "POST")) {
      const payload = await response.json().catch(() => null);
      const report = payload?.validation;
      if (typeof report?.ok === "boolean" && Array.isArray(report.violations) &&
        report.violations.every((v: Partial<ValidationReport["violations"][number]> | null) => v && typeof v.code === "string" &&
          typeof v.severity === "string" && (v.field === null || typeof v.field === "string") &&
          (v.line === null || Number.isInteger(v.line)))) throw new ApiError(response.status, report);
    }
    throw new ApiError(response.status);
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

export async function currentSession(signal: AbortSignal): Promise<Session> {
  return request<Session>("/api/session", { signal });
}

export async function login(password: string, signal: AbortSignal): Promise<Session> {
  return request<Session>("/api/login", {
    method: "POST", signal, headers: { "Content-Type": "application/json", "X-Cerebro-Request": "1" },
    body: JSON.stringify({ password }),
  });
}

export async function logout(csrf: string, signal: AbortSignal): Promise<void> {
  await request<void>("/api/logout", { method: "POST", signal, headers: { "X-CSRF-Token": csrf } });
}

/** Bound to one authenticated session. Revoking it cancels reads and pending mutations. */
export class ApiClient {
  constructor(
    private readonly session: Session,
    private readonly lifetime: AbortSignal,
    private readonly expire: () => void,
    private readonly activity: () => void,
  ) {}

  async read<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.send<T>(path, { method: "GET" }, signal);
  }

  async write<T>(path: string, method: "POST" | "PUT", body: unknown, signal?: AbortSignal): Promise<T> {
    return this.send<T>(path, {
      method, headers: { "Content-Type": "application/json", "X-CSRF-Token": this.session.csrf_token },
      body: JSON.stringify(body),
    }, signal);
  }

  private async send<T>(path: string, options: RequestInit, signal?: AbortSignal): Promise<T> {
    const combined = signal ? AbortSignal.any([signal, this.lifetime]) : this.lifetime;
    try {
      const result = await request<T>(path, { ...options, signal: combined });
      combined.throwIfAborted();
      this.activity();
      return result;
    } catch (error) {
      if (!combined.aborted && error instanceof ApiError && error.status === 401) this.expire();
      throw error;
    }
  }
}
