import { describe, expect, it, vi } from "vitest";
import {
  buildUpstreamRequestHeaders,
  isSameOriginBrowserRequest,
  parseSingleIp,
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

  describe("client address passed to the API (sign-in throttle)", () => {
    const forged = new Headers({
      host: "localhost:3000",
      "x-forwarded-for": "6.6.6.6",
      "x-real-ip": "7.7.7.7",
      forwarded: "for=8.8.8.8",
      "cf-connecting-ip": "198.51.100.23",
    });

    it("never forwards the IP headers a browser sent when no edge header is configured", () => {
      const upstream = buildUpstreamRequestHeaders(forged, {});
      expect(upstream.get("x-forwarded-for")).toBeNull();
      expect(upstream.get("x-real-ip")).toBeNull();
      expect(upstream.get("forwarded")).toBeNull();
    });

    it("passes on only the address of the edge header the deployment named, as X-Forwarded-For", () => {
      const upstream = buildUpstreamRequestHeaders(forged, { PROXY_CLIENT_IP_HEADER: "CF-Connecting-IP" });
      expect(upstream.get("x-forwarded-for")).toBe("198.51.100.23");
      expect(upstream.get("x-real-ip")).toBeNull();
      expect(upstream.get("forwarded")).toBeNull();
    });

    it("drops an edge header that is missing, a list, a host name or X-Forwarded-For itself", () => {
      const env = { PROXY_CLIENT_IP_HEADER: "x-real-ip" };
      expect(buildUpstreamRequestHeaders(new Headers(), env).get("x-forwarded-for")).toBeNull();
      expect(
        buildUpstreamRequestHeaders(new Headers({ "x-real-ip": "1.2.3.4, 5.6.7.8" }), env).get("x-forwarded-for"),
      ).toBeNull();
      expect(
        buildUpstreamRequestHeaders(new Headers({ "x-real-ip": "evil.example" }), env).get("x-forwarded-for"),
      ).toBeNull();
      expect(
        buildUpstreamRequestHeaders(forged, { PROXY_CLIENT_IP_HEADER: "x-forwarded-for" }).get("x-forwarded-for"),
      ).toBeNull();
    });

    it("accepts IPv4 and IPv6 literals only", () => {
      expect(parseSingleIp(" 203.0.113.9 ")).toBe("203.0.113.9");
      expect(parseSingleIp("2001:db8::7")).toBe("2001:db8::7");
      expect(parseSingleIp("2001:DB8::7")).toBe("2001:db8::7");
      for (const bad of ["", null, undefined, "256.1.1.1", "1.2.3", "1.2.3.4:80", "a,b", "[::1]", "x".repeat(60)]) {
        expect(parseSingleIp(bad)).toBeNull();
      }
    });

    it("normalises an IPv4-mapped IPv6 address to the IPv4 it carries, in every spelling", () => {
      expect(parseSingleIp("::ffff:203.0.113.10")).toBe("203.0.113.10");
      expect(parseSingleIp(" ::FFFF:203.0.113.10 ")).toBe("203.0.113.10");
      expect(parseSingleIp("::ffff:cb00:710a")).toBe("203.0.113.10");
      expect(parseSingleIp("0:0:0:0:0:ffff:203.0.113.10")).toBe("203.0.113.10");
      expect(parseSingleIp("::ffff:0:0")).toBe("0.0.0.0");
      for (const bad of ["::ffff:999.1.1.1", "::ffff:1.2.3", "::ffff:", ":::", "1::2::3", "gggg::1"]) {
        expect(parseSingleIp(bad)).toBeNull();
      }
      expect(parseSingleIp("::ffff:cb00")).toBe("::ffff:cb00"); // a plain IPv6 address, not a mapped IPv4 one
      // a different IPv6 prefix is a real IPv6 address and is not unwrapped
      expect(parseSingleIp("2001::ffff:203.0.113.10")).toBe("2001::ffff:203.0.113.10");
      expect(parseSingleIp("64:ff9b::cb00:710a")).toBe("64:ff9b::cb00:710a");
    });

    it("hands the API the IPv4 form of a mapped address through the edge header", () => {
      const upstream = buildUpstreamRequestHeaders(new Headers({ "x-real-ip": "::ffff:203.0.113.10" }), {
        PROXY_CLIENT_IP_HEADER: "x-real-ip",
      });
      expect(upstream.get("x-forwarded-for")).toBe("203.0.113.10");
    });

    it("sends the edge address upstream through proxyApiV1Request", async () => {
      const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response("{}"));
      const req = new Request("http://localhost:3000/api/v1/auth/login", {
        method: "POST",
        headers: { "x-forwarded-for": "6.6.6.6", "x-real-ip": "203.0.113.9" },
        body: "{}",
      });
      await proxyApiV1Request(req, fetchMock as unknown as typeof fetch, {
        BACKEND_INTERNAL_URL: "http://127.0.0.1:8000",
        PROXY_CLIENT_IP_HEADER: "x-real-ip",
      });
      const sent = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
      expect(sent.get("x-forwarded-for")).toBe("203.0.113.9");
    });
  });
});
