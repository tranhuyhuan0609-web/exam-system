import {
  ApiRequestError,
  apiFetch,
  hasAuthToken,
  isDemoModeClient,
  type ProblemDetail,
} from "./auth-client";

export interface WorkspaceCallOptions {
  url: string;
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  json?: unknown;
  fetchFn?: typeof fetch;
  apiBase?: string;
  isDemoMode?: boolean;
  redirectOn401?: boolean;
}

export type WorkspaceCallResult<T = unknown> =
  | { ok: true; data?: T; skippedOffline?: boolean }
  | { ok: false; status: number; error: string; problem: ProblemDetail };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string | null | undefined): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value.trim());
}

export function shouldHydrateLive(options: {
  hasInitialData: boolean;
  apiBase?: string;
  isDemoMode?: boolean;
  entityIds?: ReadonlyArray<string | null | undefined>;
}): boolean {
  if (options.hasInitialData) return false;
  if (options.apiBase === "__OFFLINE__") return false;
  if (isDemoModeClient(options.isDemoMode)) return false;
  if (!hasAuthToken()) return false;
  if (options.entityIds && options.entityIds.length > 0) {
    return options.entityIds.every((id) => isUuid(id));
  }
  return true;
}

export function shouldSyncLiveEntity(options: {
  fetchFn?: typeof fetch;
  apiBase?: string;
  isDemoMode?: boolean;
  entityIds?: ReadonlyArray<string | null | undefined>;
}): boolean {
  if (options.apiBase === "__OFFLINE__") return false;
  if (options.fetchFn) return true;
  if (isDemoModeClient(options.isDemoMode)) return false;
  if (!hasAuthToken()) return false;
  if (options.entityIds && options.entityIds.length > 0) {
    return options.entityIds.every((id) => isUuid(id));
  }
  return true;
}

export async function workspaceCall<T = unknown>({
  url,
  method = "POST",
  json,
  fetchFn,
  apiBase = "",
  isDemoMode,
  redirectOn401 = true,
}: WorkspaceCallOptions): Promise<WorkspaceCallResult<T>> {
  const caller = fetchFn ?? (typeof fetch === "function" ? fetch : null);
  if (!caller || apiBase === "__OFFLINE__") {
    return { ok: true, skippedOffline: true };
  }

  try {
    const res = await apiFetch(`${apiBase}${url}`, {
      method,
      fetchFn: caller,
      json,
      redirectOn401,
      throwOnError: true,
    });
    const contentType = res.headers?.get?.("content-type") ?? "";
    if (contentType.includes("json")) {
      const data = (await res.json()) as T;
      return { ok: true, data };
    }
    const text = typeof res.text === "function" ? await res.text() : "";
    if (text.trim().startsWith("{") || text.trim().startsWith("[")) {
      try {
        return { ok: true, data: JSON.parse(text) as T };
      } catch {
        return { ok: true, data: text as unknown as T };
      }
    }
    return { ok: true, data: (text || undefined) as T | undefined };
  } catch (error) {
    if (error instanceof ApiRequestError) {
      return {
        ok: false,
        status: error.status,
        error: error.message,
        problem: error.problem,
      };
    }
    if (isDemoModeClient(isDemoMode)) {
      return { ok: true, skippedOffline: true };
    }
    const networkProblem: ProblemDetail = {
      status: 0,
      title: "Network Error",
      detail: "Không thể kết nối tới máy chủ Backend API (/api/v1).",
    };
    return {
      ok: false,
      status: 0,
      error: "[Network Error] Không thể kết nối tới máy chủ Backend API (/api/v1).",
      problem: networkProblem,
    };
  }
}
