/**
 * Helpers for the Next.js `/api/v1/[...path]` proxy to FastAPI.
 *
 * The legacy Jinja routes on port 8000 set an `HttpOnly` `exam_session` cookie on `localhost`. Because browsers share
 * cookies across ports on the same host, Next.js requests on port 3000 inherit that unrelated cookie. FastAPI's
 * `CSRFProtectMiddleware` treats any `/api/v1` request carrying `exam_session` as cookie-authenticated and rejects the
 * cross-port proxy `Origin != Host` mismatch with `403`. Stripping `exam_session` exclusively for same-origin Next.js
 * requests keeps cross-site CSRF protection intact while allowing JWT `/api/v1` calls (including login) to succeed.
 */

export const LEGACY_SESSION_COOKIE = "exam_session";

const HOP_BY_HOP_REQUEST_HEADERS = new Set([
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const STRIPPED_RESPONSE_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "set-cookie",
  "transfer-encoding",
]);

export type BackendEnv = Record<string, string | undefined>;

export function getBackendBaseUrl(env: BackendEnv = process.env): string {
  return (env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}

export function resolveUpstreamUrl(
  requestUrl: string,
  env: BackendEnv = process.env,
): string {
  const incoming = new URL(requestUrl);
  return `${getBackendBaseUrl(env)}${incoming.pathname}${incoming.search}`;
}

export function isSameOriginBrowserRequest(headers: Headers): boolean {
  const origin = headers.get("origin");
  if (!origin) return true;

  const rawHost = headers.get("x-forwarded-host") || headers.get("host") || "";
  const requestHost = rawHost.split(",")[0]?.trim().toLowerCase() ?? "";
  if (!requestHost) return false;

  try {
    return new URL(origin).host.toLowerCase() === requestHost;
  } catch {
    return false;
  }
}

export function stripCookieName(cookieHeader: string, cookieName: string): string {
  const kept: string[] = [];
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const eqIndex = trimmed.indexOf("=");
    const name = (eqIndex >= 0 ? trimmed.slice(0, eqIndex) : trimmed).trim();
    if (name !== cookieName) {
      kept.push(trimmed);
    }
  }
  return kept.join("; ");
}

export function buildUpstreamRequestHeaders(incoming: Headers): Headers {
  const out = new Headers();
  incoming.forEach((value, key) => {
    if (!HOP_BY_HOP_REQUEST_HEADERS.has(key.toLowerCase())) {
      out.append(key, value);
    }
  });

  if (isSameOriginBrowserRequest(incoming)) {
    const rawCookie = incoming.get("cookie");
    if (rawCookie !== null) {
      const cleaned = stripCookieName(rawCookie, LEGACY_SESSION_COOKIE);
      if (cleaned) {
        out.set("cookie", cleaned);
      } else {
        out.delete("cookie");
      }
    }
  }

  return out;
}

export function buildDownstreamResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  upstream.forEach((value, key) => {
    if (!STRIPPED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      out.append(key, value);
    }
  });

  const maybeGetSetCookie = (upstream as unknown as { getSetCookie?: () => string[] }).getSetCookie;
  if (typeof maybeGetSetCookie === "function") {
    for (const cookie of maybeGetSetCookie.call(upstream)) {
      out.append("set-cookie", cookie);
    }
  } else {
    const single = upstream.get("set-cookie");
    if (single) {
      out.append("set-cookie", single);
    }
  }

  return out;
}

export async function proxyApiV1Request(
  request: Request,
  fetchFn: typeof fetch = fetch,
  env: BackendEnv = process.env,
): Promise<Response> {
  const targetUrl = resolveUpstreamUrl(request.url, env);
  const headers = buildUpstreamRequestHeaders(request.headers);
  const method = request.method.toUpperCase();

  let body: ArrayBuffer | undefined;
  if (method !== "GET" && method !== "HEAD") {
    const buffer = await request.arrayBuffer();
    if (buffer.byteLength > 0) {
      body = buffer;
    }
  }

  try {
    const upstream = await fetchFn(targetUrl, {
      method,
      headers,
      body,
      redirect: "manual",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: buildDownstreamResponseHeaders(upstream.headers),
    });
  } catch {
    return new Response(
      JSON.stringify({
        title: "Bad Gateway",
        status: 502,
        detail: "Không thể kết nối tới máy chủ Backend API.",
        error_code: "UPSTREAM_UNREACHABLE",
      }),
      {
        status: 502,
        headers: { "content-type": "application/problem+json" },
      },
    );
  }
}
