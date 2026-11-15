import { describe, expect, it, vi } from "vitest";
import {
  buildUpstreamRequestHeaders,
  isSameOriginBrowserRequest,
  proxyApiV1Request,
  resolveUpstreamUrl,
  stripCookieName,
} from "../backend-proxy";

describe("Next.js /api/v1 backend proxy", () => {
  it("strips legacy exam_session cookie on same-origin requests while preserving other cookies", () => {
    expect(stripCookieName("exam_session=abc; ulp_access_token=tok==; theme=light", "exam_session")).toBe(
      "ulp_access_token=tok==; theme=light",
    );
    expect(stripCookieName("exam_session=only_cookie", "exam_session")).toBe("");
  });

  it("drops exam_session from same-origin /api/v1 requests so CSRF does not trip across localhost ports", () => {
    const incoming = new Headers({
      host: "localhost:3000",
      origin: "http://localhost:3000",
      cookie: "exam_session=legacy_html_cookie; ulp_access_token=jwt.part.sig",
      authorization: "Bearer jwt.part.sig",
    });

    expect(isSameOriginBrowserRequest(incoming)).toBe(true);
    const upstream = buildUpstreamRequestHeaders(incoming);
    expect(upstream.get("cookie")).toBe("ulp_access_token=jwt.part.sig");
    expect(upstream.get("host")).toBeNull();
    expect(upstream.get("origin")).toBe("http://localhost:3000");
    expect(upstream.get("authorization")).toBe("Bearer jwt.part.sig");
  });

  it("keeps cookies intact on cross-origin requests so backend CSRF protection still blocks them", () => {
    const incoming = new Headers({
      host: "localhost:3000",
      origin: "https://evil.example",
      cookie: "exam_session=legacy_html_cookie",
    });

    expect(isSameOriginBrowserRequest(incoming)).toBe(false);
    const upstream = buildUpstreamRequestHeaders(incoming);
    expect(upstream.get("cookie")).toBe("exam_session=legacy_html_cookie");
  });

  it("forwards query string, JSON body, and 429/Problem headers cleanly", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      return new Response(JSON.stringify({ access_token: "abc" }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "cache-control": "no-store",
          "x-correlation-id": "cid-1",
          "content-encoding": "gzip",
        },
      });
    });

    const req = new Request("http://localhost:3000/api/v1/auth/login?foo=bar", {
      method: "POST",
      headers: {
        host: "localhost:3000",
        origin: "http://localhost:3000",
        cookie: "exam_session=stale_cookie",
        "content-type": "application/json",
      },
      body: JSON.stringify({ email: "teacher@h2edu.demo", password: "Demo@12345" }),
    });

    expect(resolveUpstreamUrl(req.url, { BACKEND_INTERNAL_URL: "http://127.0.0.1:8000/" })).toBe(
      "http://127.0.0.1:8000/api/v1/auth/login?foo=bar",
    );

    const res = await proxyApiV1Request(req, fetchMock as unknown as typeof fetch, {
      BACKEND_INTERNAL_URL: "http://127.0.0.1:8000",
    });

    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("x-correlation-id")).toBe("cid-1");
    expect(res.headers.get("content-encoding")).toBeNull();
    expect(await res.json()).toEqual({ access_token: "abc" });

    const forwardedInit = fetchMock.mock.calls[0]?.[1];
    expect((forwardedInit?.headers as Headers).get("cookie")).toBeNull();
  });

  it("returns RFC 7807 502 Problem+JSON when backend is unreachable", async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const req = new Request("http://localhost:3000/api/v1/auth/login", { method: "POST" });
    const res = await proxyApiV1Request(req, fetchMock as unknown as typeof fetch);
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toContain("application/problem+json");
    const body = (await res.json()) as { error_code: string };
    expect(body.error_code).toBe("UPSTREAM_UNREACHABLE");
  });
});
