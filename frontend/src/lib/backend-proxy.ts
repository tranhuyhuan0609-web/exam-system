/**
 * Helpers for the Next.js `/api/v1/[...path]` proxy to FastAPI.
 *
 * The legacy Jinja routes on port 8000 set an `HttpOnly` `exam_session` cookie on `localhost`. Because browsers share
 * cookies across ports on the same host, Next.js requests on port 3000 inherit that unrelated cookie. FastAPI's
 * `CSRFProtectMiddleware` treats any `/api/v1` request carrying `exam_session` as cookie-authenticated and rejects the
 * cross-port proxy `Origin != Host` mismatch with `403`. Stripping `exam_session` exclusively for same-origin Next.js
 * requests keeps cross-site CSRF protection intact while allowing JWT `/api/v1` calls (including login) to succeed.
 *
 * Client address. FastAPI throttles sign-in per client address, and sees only this server as the TCP peer. The IP
 * headers a browser sends are never forwarded: Next.js keeps a forged `X-Forwarded-For` as it came (it fills the
 * header with the socket address only when the client sent none), so they cannot be told from real ones. The address
 * is passed on only when the deployment names, in `PROXY_CLIENT_IP_HEADER`, a header that its own edge (nginx,
 * Cloudflare, a load balancer) always overwrites, e.g. `x-real-ip` or `cf-connecting-ip`; it is then sent upstream as
 * `X-Forwarded-For`. The API believes that only when this server is in its `TRUSTED_PROXIES`. Unset: every visitor
 * reaches the API as this server, which is safe and merely coarse.
 */

import { isIP } from "node:net";

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

/** Headers that tell the API who the client is: only this proxy may set them. */
const CLIENT_IP_HEADERS = ["x-forwarded-for", "x-real-ip", "forwarded"] as const;

const IPV4 = /^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

// "::ffff:203.0.113.10" or "::ffff:cb00:710a": an IPv4 client seen through a dual-stack socket
const IPV4_MAPPED = /^(?:::|(?:0{1,4}:){5})ffff:(.+)$/i;
const HEXTET_PAIR = /^([0-9a-f]{1,4}):([0-9a-f]{1,4})$/i;

function unmapIpv4(value: string): string | null {
  const tail = IPV4_MAPPED.exec(value)?.[1];
  if (tail === undefined) return null;
  if (IPV4.test(tail)) return tail;
  const pair = HEXTET_PAIR.exec(tail);
  if (!pair) return null;
  const high = parseInt(pair[1] as string, 16);
  const low = parseInt(pair[2] as string, 16);
  return `${high >> 8}.${high & 255}.${low >> 8}.${low & 255}`;
}

/**
 * A single IPv4/IPv6 address (no list, no port, no host name), else null. An IPv4-mapped IPv6 address is returned as
 * the IPv4 address it carries, so one client is one counter whichever way the edge wrote it.
 */
export function parseSingleIp(raw: string | null | undefined): string | null {
  const value = (raw ?? "").trim();
  if (IPV4.test(value)) return value;
  if (value.includes(":") && !value.includes(",")) {
    const mapped = unmapIpv4(value);
    if (mapped !== null) return mapped;
    const bare = value.replace(/%.*$/, ""); // a zone id ("fe80::1%eth0") is local to one host
    if (isIP(bare) === 6) return bare.toLowerCase();
  }
  return null;
}

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

export function buildUpstreamRequestHeaders(incoming: Headers, env: BackendEnv = process.env): Headers {
  const out = new Headers();
  incoming.forEach((value, key) => {
    const name = key.toLowerCase();
    if (!HOP_BY_HOP_REQUEST_HEADERS.has(name) && !(CLIENT_IP_HEADERS as readonly string[]).includes(name)) {
      out.append(key, value);
    }
  });

  const edgeHeader = (env.PROXY_CLIENT_IP_HEADER || "").trim().toLowerCase();
  // a header a browser can also send as a chain is no proof of anything: only a dedicated edge header counts
  if (edgeHeader && edgeHeader !== "x-forwarded-for" && edgeHeader !== "forwarded") {
    const clientIp = parseSingleIp(incoming.get(edgeHeader));
    if (clientIp) out.set("x-forwarded-for", clientIp);
  }

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
  const headers = buildUpstreamRequestHeaders(request.headers, env);
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
