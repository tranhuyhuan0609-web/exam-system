export const AUTH_STORAGE_KEY = "ulp_auth_session_v1";
export const AUTH_COOKIE_NAME = "ulp_access_token";
export const AUTH_CHANGED_EVENT = "ulp-auth-changed";

export interface AuthSession {
  accessToken: string;
  userId: string;
  tenantId: string;
  membershipId?: string | null;
  roles: string[];
  entitlementTier?: string | null;
  email?: string | null;
  displayName?: string | null;
  expiresAtEpoch?: number | null;
}

export interface DemoAccountPreset {
  id: "learner" | "teacher" | "tenant_admin" | "platform_admin";
  label: string;
  roleBadge: string;
  email: string;
  password: string;
  tenantSlug: string;
  description: string;
  fallbackSession: Omit<AuthSession, "accessToken">;
}

/** The accounts `python -m app.ops.seed_demo` creates in tenant `h2edu-demo` (one password, localhost only). */
export const DEMO_PASSWORD = "Demo@12345";
export const DEMO_TENANT_SLUG = "h2edu-demo";

export const DEMO_ACCOUNTS: readonly DemoAccountPreset[] = [
  {
    id: "learner",
    label: "Học sinh THPT",
    roleBadge: "LEARNER",
    email: "learner@h2edu.demo",
    password: DEMO_PASSWORD,
    tenantSlug: DEMO_TENANT_SLUG,
    description: "Học liệu 4 cấp, Sổ tay câu sai, Phòng thi THPTQG",
    fallbackSession: {
      userId: "demo-learner-001",
      tenantId: "tenant-h2edu-demo",
      membershipId: "mem-learner-001",
      roles: ["LEARNER"],
      entitlementTier: "STUDENT_PRO",
      email: "learner@h2edu.demo",
      displayName: "Minh (HS Demo)",
    },
  },
  {
    id: "teacher",
    label: "Giáo viên & Biên soạn",
    roleBadge: "TEACHER",
    email: "teacher@h2edu.demo",
    password: DEMO_PASSWORD,
    tenantSlug: DEMO_TENANT_SLUG,
    description: "Duyệt BBox, Block Studio, Ma trận đề & Lớp học 12A1",
    fallbackSession: {
      userId: "demo-teacher-001",
      tenantId: "tenant-h2edu-demo",
      membershipId: "mem-teacher-001",
      roles: ["TEACHER"],
      entitlementTier: "TEACHER_PRO",
      email: "teacher@h2edu.demo",
      displayName: "Cô Lan (GV Demo)",
    },
  },
  {
    id: "tenant_admin",
    label: "Quản trị trường",
    roleBadge: "TENANT_ADMIN",
    email: "tenant_admin@h2edu.demo",
    password: DEMO_PASSWORD,
    tenantSlug: DEMO_TENANT_SLUG,
    description: "Outbox/DLQ, Feature Flags, Drive Pairing & DMCA Takedowns",
    fallbackSession: {
      userId: "demo-tenant-admin-001",
      tenantId: "tenant-h2edu-demo",
      membershipId: "mem-tenant-admin-001",
      roles: ["TENANT_ADMIN"],
      entitlementTier: "SCHOOL_ENTERPRISE",
      email: "tenant_admin@h2edu.demo",
      displayName: "Tenant Admin (Demo)",
    },
  },
  {
    id: "platform_admin",
    label: "Quản trị nền tảng",
    roleBadge: "PLATFORM_ADMIN",
    email: "platform_admin@h2edu.demo",
    password: DEMO_PASSWORD,
    tenantSlug: DEMO_TENANT_SLUG,
    description: "Khung đánh giá, hiệu chuẩn IRT, gói vertical (chỉ quản trị nền tảng)",
    fallbackSession: {
      userId: "demo-platform-admin-001",
      tenantId: "tenant-h2edu-demo",
      membershipId: "mem-platform-admin-001",
      roles: ["PLATFORM_ADMIN"],
      entitlementTier: "SCHOOL_ENTERPRISE",
      email: "platform_admin@h2edu.demo",
      displayName: "Platform Admin (Demo)",
    },
  },
];

export interface JwtClaims {
  sub?: string;
  tenant_id?: string;
  membership_id?: string;
  roles?: unknown;
  entitlement_tier?: string;
  email?: string;
  name?: string;
  exp?: number;
  [key: string]: unknown;
}

export interface ProblemDetail {
  type?: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  error_code?: string;
  correlation_id?: string;
  extensions?: Record<string, unknown>;
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly problem: ProblemDetail;
  readonly errorCode?: string;
  readonly correlationId?: string;

  constructor(problem: ProblemDetail) {
    super(formatProblemMessage(problem));
    this.name = "ApiRequestError";
    this.status = problem.status;
    this.problem = problem;
    this.errorCode = problem.error_code;
    this.correlationId = problem.correlation_id;
  }
}

export interface ApiFetchOptions extends RequestInit {
  fetchFn?: typeof fetch;
  token?: string | null;
  json?: unknown;
  redirectOn401?: boolean;
  throwOnError?: boolean;
}

const STATUS_FALLBACK_GUIDANCE: Record<number, { title: string; detail: string }> = {
  401: {
    title: "Unauthorized",
    detail: "Phiên đăng nhập đã hết hạn hoặc không hợp lệ. Vui lòng đăng nhập lại.",
  },
  403: {
    title: "Forbidden",
    detail: "Bạn không có quyền truy cập tài nguyên này hoặc sai phạm vi tenant (RBAC/RLS).",
  },
  404: {
    title: "Not Found",
    detail: "Không tìm thấy tài nguyên hoặc endpoint chưa được hỗ trợ trên backend.",
  },
  409: {
    title: "Conflict",
    detail: "Xung đột dữ liệu hoặc trạng thái đồng thời. Vui lòng tải lại trạng thái mới nhất.",
  },
  422: {
    title: "Unprocessable Entity",
    detail: "Dữ liệu gửi lên không đúng định dạng hoặc thiếu trường bắt buộc.",
  },
  429: {
    title: "Too Many Requests",
    detail: "Hệ thống đang giới hạn tần suất (Rate Limit). Vui lòng đợi vài giây rồi thử lại.",
  },
};

type AuthNavigationHandler = (loginUrl: string) => void;

let customNavigationHandler: AuthNavigationHandler | null = null;

export function setAuthNavigationHandler(handler: AuthNavigationHandler | null): void {
  customNavigationHandler = handler;
}

function decodeBase64UrlToUtf8(input: string): string {
  const normalized = input.replace(/-/g, "+").replace(/_/g, "/");
  const padding = normalized.length % 4 === 0 ? "" : "=".repeat(4 - (normalized.length % 4));
  const padded = normalized + padding;

  if (typeof globalThis.atob === "function") {
    const binary = globalThis.atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new TextDecoder("utf-8").decode(bytes);
  }
  if (typeof Buffer !== "undefined") {
    return Buffer.from(padded, "base64").toString("utf-8");
  }
  throw new Error("No base64 decoder available");
}

export function decodeJwtClaims(token: string): JwtClaims | null {
  const parts = token.trim().split(".");
  const payloadSegment = parts[1];
  if (parts.length < 2 || !payloadSegment) return null;
  try {
    const rawJson = decodeBase64UrlToUtf8(payloadSegment);
    const parsed = JSON.parse(rawJson);
    if (!parsed || typeof parsed !== "object") return null;
    return parsed as JwtClaims;
  } catch {
    return null;
  }
}

export function isSessionExpired(session: AuthSession | null | undefined, nowEpochSec?: number): boolean {
  if (!session) return true;
  if (typeof session.expiresAtEpoch !== "number" || !Number.isFinite(session.expiresAtEpoch)) {
    return false;
  }
  const nowSec = nowEpochSec ?? Math.floor(Date.now() / 1000);
  return session.expiresAtEpoch <= nowSec;
}

function normalizeRoles(rawRoles: unknown): string[] {
  if (Array.isArray(rawRoles)) {
    return rawRoles
      .map((item) => String(item).trim())
      .filter((item) => item.length > 0);
  }
  if (typeof rawRoles === "string" && rawRoles.trim()) {
    return rawRoles
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }
  return [];
}

export interface LoginPayloadLike {
  access_token?: string;
  token?: string;
  user_id?: string;
  sub?: string;
  tenant_id?: string;
  membership_id?: string | null;
  roles?: string[];
  entitlement_tier?: string | null;
  email?: string | null;
  display_name?: string | null;
  expires_at?: number | null;
  expires_in?: number | null;
}

export function buildSessionFromLoginResponse(
  payload: LoginPayloadLike,
  fallbackEmail?: string,
): AuthSession | null {
  const accessToken = (payload.access_token || payload.token || "").trim();
  if (!accessToken) return null;
  const claims = decodeJwtClaims(accessToken);

  const roles = normalizeRoles(payload.roles ?? claims?.roles);
  const expiresAtEpoch =
    typeof payload.expires_at === "number"
      ? payload.expires_at
      : typeof payload.expires_in === "number"
        ? Math.floor(Date.now() / 1000) + payload.expires_in
        : typeof claims?.exp === "number"
          ? claims.exp
          : null;

  return {
    accessToken,
    userId: String(payload.user_id || payload.sub || claims?.sub || "authenticated-user"),
    tenantId: String(payload.tenant_id || claims?.tenant_id || "default-tenant"),
    membershipId: (payload.membership_id ?? claims?.membership_id ?? null) as string | null,
    roles: roles.length > 0 ? roles : ["USER"],
    entitlementTier: (payload.entitlement_tier ?? claims?.entitlement_tier ?? null) as string | null,
    email: (payload.email ?? claims?.email ?? fallbackEmail ?? null) as string | null,
    displayName: (payload.display_name ?? claims?.name ?? null) as string | null,
    expiresAtEpoch,
  };
}

export function buildSessionFromRawToken(rawToken: string): AuthSession | null {
  const accessToken = rawToken.trim();
  if (!accessToken) return null;
  const claims = decodeJwtClaims(accessToken);
  if (!claims) return null;

  const roles = normalizeRoles(claims.roles);
  return {
    accessToken,
    userId: String(claims.sub || "jwt-user"),
    tenantId: String(claims.tenant_id || "default-tenant"),
    membershipId: claims.membership_id ? String(claims.membership_id) : null,
    roles: roles.length > 0 ? roles : ["STUDENT"],
    entitlementTier: claims.entitlement_tier ? String(claims.entitlement_tier) : null,
    email: claims.email ? String(claims.email) : null,
    displayName: claims.name ? String(claims.name) : null,
    expiresAtEpoch: typeof claims.exp === "number" ? claims.exp : null,
  };
}

let memorySessionCache: AuthSession | null = null;

function getSafeStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    const candidate = window.localStorage;
    if (
      candidate &&
      typeof candidate.getItem === "function" &&
      typeof candidate.setItem === "function" &&
      typeof candidate.removeItem === "function"
    ) {
      return candidate;
    }
  } catch {
    // Ignore restricted/uninitialized localStorage
  }
  return null;
}

function readTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  try {
    const rawCookie = document.cookie || "";
    const prefix = `${AUTH_COOKIE_NAME}=`;
    for (const part of rawCookie.split(";")) {
      const trimmed = part.trim();
      if (trimmed.startsWith(prefix)) {
        const val = decodeURIComponent(trimmed.slice(prefix.length)).trim();
        return val || null;
      }
    }
  } catch {
    // Ignore cookie parse failures
  }
  return null;
}

function dispatchAuthChanged(): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  } catch {
    // Ignore environments without Event constructor
  }
}

export function getAuthSession(): AuthSession | null {
  if (typeof window === "undefined") return null;

  const storage = getSafeStorage();
  if (storage) {
    try {
      const raw = storage.getItem(AUTH_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as AuthSession;
        if (parsed && typeof parsed.accessToken === "string" && parsed.accessToken.trim()) {
          if (isSessionExpired(parsed)) {
            memorySessionCache = null;
            return null;
          }
          memorySessionCache = parsed;
          return parsed;
        }
      }
    } catch {
      // Fall back to in-memory / cookie session
    }
  }

  if (memorySessionCache) {
    if (isSessionExpired(memorySessionCache)) {
      memorySessionCache = null;
      return null;
    }
    return memorySessionCache;
  }

  const cookieToken = readTokenFromCookie();
  if (cookieToken) {
    const rebuilt = buildSessionFromRawToken(cookieToken);
    if (rebuilt && !isSessionExpired(rebuilt)) {
      memorySessionCache = rebuilt;
      return rebuilt;
    }
  }

  return null;
}

export function setAuthSession(session: AuthSession): void {
  if (typeof window === "undefined") return;
  memorySessionCache = session;
  const storage = getSafeStorage();
  if (storage) {
    try {
      storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
    } catch {
      // Ignore storage quota / disabled storage
    }
  }
  if (typeof document !== "undefined") {
    try {
      const encoded = encodeURIComponent(session.accessToken);
      const maxAgeAttr =
        typeof session.expiresAtEpoch === "number"
          ? `; Max-Age=${Math.max(0, Math.floor(session.expiresAtEpoch - Date.now() / 1000))}`
          : "";
      document.cookie = `${AUTH_COOKIE_NAME}=${encoded}; Path=/; SameSite=Lax${maxAgeAttr}`;
    } catch {
      // Ignore cookie write failures
    }
  }
  dispatchAuthChanged();
}

export function clearAuthSession(): void {
  memorySessionCache = null;
  if (typeof window === "undefined") return;
  const storage = getSafeStorage();
  if (storage) {
    try {
      storage.removeItem(AUTH_STORAGE_KEY);
    } catch {
      // Ignore storage errors
    }
  }
  if (typeof document !== "undefined") {
    try {
      document.cookie = `${AUTH_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax`;
    } catch {
      // Ignore cookie errors
    }
  }
  dispatchAuthChanged();
}

export function getAccessToken(): string | null {
  return getAuthSession()?.accessToken ?? null;
}

export function hasAuthToken(): boolean {
  return Boolean(getAccessToken());
}

export function sanitizeRedirectTarget(target: string | null | undefined): string {
  if (!target) return "/";
  const trimmed = target.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//")) {
    return "/";
  }
  return trimmed;
}

export function hasDemoQueryFlag(search?: string): boolean {
  const rawSearch =
    search !== undefined
      ? search
      : typeof window !== "undefined"
        ? window.location.search
        : "";
  if (!rawSearch) return false;
  try {
    const params = new URLSearchParams(rawSearch.startsWith("?") ? rawSearch : `?${rawSearch}`);
    return params.get("demo") === "1";
  } catch {
    return false;
  }
}

export function isDemoModeClient(propDemo?: boolean, search?: string): boolean {
  if (propDemo) return true;
  return hasDemoQueryFlag(search);
}

export function isDevAuthShortcutsEnabled(search?: string): boolean {
  if (hasDemoQueryFlag(search)) return true;
  if (process.env.NEXT_PUBLIC_ENABLE_DEMO_LOGIN === "1") return true;
  return process.env.NODE_ENV !== "production";
}

export function navigateToLogin(nextPath?: string): void {
  const safeNext =
    nextPath ??
    (typeof window !== "undefined"
      ? `${window.location.pathname || "/"}${window.location.search || ""}`
      : "/");
  const target =
    safeNext && safeNext !== "/dang-nhap" && !safeNext.startsWith("/dang-nhap?")
      ? `/dang-nhap?next=${encodeURIComponent(sanitizeRedirectTarget(safeNext))}`
      : "/dang-nhap";

  if (customNavigationHandler) {
    customNavigationHandler(target);
    return;
  }
  if (typeof window !== "undefined" && window.location) {
    if (window.location.pathname === "/dang-nhap") return;
    try {
      window.location.assign(target);
    } catch {
      // Ignore jsdom unimplemented navigation warnings
    }
  }
}

function extractProblemDetailText(
  parsed: Record<string, unknown>,
  fallbackDetail: string,
): string {
  if (typeof parsed.detail === "string" && parsed.detail.trim()) {
    return parsed.detail;
  }
  if (Array.isArray(parsed.detail)) {
    const formatted = parsed.detail
      .map((entry) => {
        if (typeof entry === "string" && entry.trim()) return entry.trim();
        if (!entry || typeof entry !== "object") return "";
        const rec = entry as { loc?: unknown; msg?: unknown };
        const msg = typeof rec.msg === "string" ? rec.msg.trim() : "";
        const rawLoc = Array.isArray(rec.loc)
          ? rec.loc.filter((part): part is string | number => typeof part === "string" || typeof part === "number")
          : [];
        const cleanLoc =
          rawLoc.length > 1 && (rawLoc[0] === "body" || rawLoc[0] === "query" || rawLoc[0] === "path")
            ? rawLoc.slice(1)
            : rawLoc;
        const field = cleanLoc.join(".");
        if (field && msg) return `${field}: ${msg}`;
        return msg || field;
      })
      .filter((item) => item.length > 0);
    if (formatted.length > 0) {
      return formatted.join("; ");
    }
  }
  if (typeof parsed.message === "string" && parsed.message.trim()) {
    return parsed.message;
  }
  return fallbackDetail;
}

export async function parseProblemDetail(response: Response): Promise<ProblemDetail> {
  const status = response.status || 500;
  const fallback = STATUS_FALLBACK_GUIDANCE[status] ?? {
    title: response.statusText || (status >= 500 ? "Internal Server Error" : "Request Failed"),
    detail: `Yêu cầu thất bại với mã trạng thái HTTP ${status}.`,
  };

  let rawBody = "";
  try {
    rawBody = typeof response.text === "function" ? await response.text() : "";
  } catch {
    rawBody = "";
  }

  if (rawBody.trim()) {
    try {
      const parsed = JSON.parse(rawBody) as Record<string, unknown>;
      if (parsed && typeof parsed === "object") {
        const knownKeys = new Set([
          "type",
          "title",
          "status",
          "detail",
          "instance",
          "error_code",
          "correlation_id",
        ]);
        const extensions: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(parsed)) {
          if (!knownKeys.has(k)) extensions[k] = v;
        }
        return {
          type: typeof parsed.type === "string" ? parsed.type : undefined,
          title: typeof parsed.title === "string" && parsed.title ? parsed.title : fallback.title,
          status: typeof parsed.status === "number" ? parsed.status : status,
          detail: extractProblemDetailText(parsed, fallback.detail),
          instance: typeof parsed.instance === "string" ? parsed.instance : undefined,
          error_code:
            typeof parsed.error_code === "string"
              ? parsed.error_code
              : typeof parsed.code === "string"
                ? parsed.code
                : undefined,
          correlation_id:
            typeof parsed.correlation_id === "string" ? parsed.correlation_id : undefined,
          extensions: Object.keys(extensions).length > 0 ? extensions : undefined,
        };
      }
    } catch {
      return {
        title: fallback.title,
        status,
        detail: rawBody.slice(0, 240),
      };
    }
  }

  return {
    title: fallback.title,
    status,
    detail: fallback.detail,
  };
}

export function formatProblemMessage(problem: ProblemDetail): string {
  const head = `[${problem.status} ${problem.title}]`;
  const detail = problem.detail || "";
  const extras: string[] = [];
  if (problem.error_code) extras.push(`Mã lỗi: ${problem.error_code}`);
  if (problem.correlation_id) extras.push(`Trace: ${problem.correlation_id}`);
  const suffix = extras.length > 0 ? ` (${extras.join(" • ")})` : "";
  return `${head} ${detail}${suffix}`.trim();
}

export function buildAuthHeaders(
  headers?: HeadersInit,
  explicitToken?: string | null,
  hasJsonBody?: boolean,
): Record<string, string> | undefined {
  const result: Record<string, string> = {};
  if (headers) {
    if (typeof Headers !== "undefined" && headers instanceof Headers) {
      headers.forEach((value, key) => {
        result[key] = value;
      });
    } else if (Array.isArray(headers)) {
      for (const [key, value] of headers) {
        result[key] = value;
      }
    } else {
      Object.assign(result, headers);
    }
  }

  const token = explicitToken !== undefined ? explicitToken : getAccessToken();
  const hasAuthHeader = Object.keys(result).some((k) => k.toLowerCase() === "authorization");
  if (token && !hasAuthHeader) {
    result["Authorization"] = `Bearer ${token}`;
  }

  if (hasJsonBody) {
    const hasContentType = Object.keys(result).some((k) => k.toLowerCase() === "content-type");
    if (!hasContentType) {
      result["Content-Type"] = "application/json";
    }
  }

  return Object.keys(result).length > 0 ? result : undefined;
}

export async function apiFetch(input: string, options: ApiFetchOptions = {}): Promise<Response> {
  const {
    fetchFn,
    token,
    json,
    redirectOn401 = true,
    throwOnError = true,
    headers,
    body,
    method,
    ...restInit
  } = options;

  const caller = fetchFn ?? fetch;
  const resolvedHeaders = buildAuthHeaders(headers, token, json !== undefined);
  const resolvedBody = json !== undefined ? JSON.stringify(json) : body;
  const normalizedMethod = (method ?? (resolvedBody !== undefined ? "POST" : "GET")).toUpperCase();

  let response: Response;
  if (normalizedMethod === "GET" && !resolvedHeaders && resolvedBody === undefined && Object.keys(restInit).length === 0) {
    response = await caller(input);
  } else {
    const init: RequestInit = {
      ...restInit,
      ...(method ? { method: normalizedMethod } : {}),
      ...(resolvedHeaders ? { headers: resolvedHeaders } : {}),
      ...(resolvedBody !== undefined ? { body: resolvedBody } : {}),
    };
    response = await caller(input, init);
  }

  if (!response.ok) {
    const problem = await parseProblemDetail(response);
    if (response.status === 401) {
      clearAuthSession();
      if (redirectOn401) {
        navigateToLogin();
      }
    }
    if (throwOnError) {
      throw new ApiRequestError(problem);
    }
  }

  return response;
}
