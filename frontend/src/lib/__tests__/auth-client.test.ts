// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  hydrateAnswersFromSyncPayload,
  mapPaperPayloadToExamAttempt,
} from "@/components/exam-room/paper-adapter";
import { createEmptyAnswerState } from "@/components/exam-room/types";
import {
  ApiRequestError,
  apiFetch,
  AUTH_CHANGED_EVENT,
  AUTH_COOKIE_NAME,
  buildSessionFromLoginResponse,
  buildSessionFromRawToken,
  clearAuthSession,
  DEMO_ACCOUNTS,
  DEMO_PASSWORD,
  DEMO_TENANT_SLUG,
  decodeJwtClaims,
  formatProblemMessage,
  getAccessToken,
  getAuthSession,
  parseProblemDetail,
  sanitizeRedirectTarget,
  setAuthNavigationHandler,
  setAuthSession,
} from "@/lib/auth-client";

function encodeFakeJwt(payload: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const body = Buffer.from(JSON.stringify(payload), "utf-8").toString("base64url");
  return `${header}.${body}.sig`;
}

afterEach(() => {
  setAuthNavigationHandler(null);
  clearAuthSession();
});

describe("Auth Client, JWT Session & RFC 7807 Problem+JSON", () => {
  it("decodes JWT claims, builds AuthSession, persists localStorage + cookie, and clears cleanly", () => {
    const token = encodeFakeJwt({
      sub: "user-42",
      tenant_id: "tenant-alpha",
      membership_id: "mem-42",
      roles: ["STUDENT", "PRO_MEMBER"],
      entitlement_tier: "STUDENT_PRO",
      email: "minh@h2edu.vn",
      name: "Nguyễn Minh",
      exp: Math.floor(Date.now() / 1000) + 3600,
    });

    const claims = decodeJwtClaims(token);
    expect(claims?.sub).toBe("user-42");
    expect(claims?.name).toBe("Nguyễn Minh");

    const session = buildSessionFromRawToken(token);
    expect(session).not.toBeNull();
    expect(session?.tenantId).toBe("tenant-alpha");
    expect(session?.roles).toEqual(["STUDENT", "PRO_MEMBER"]);

    const eventsFired: string[] = [];
    const listener = () => eventsFired.push("changed");
    window.addEventListener(AUTH_CHANGED_EVENT, listener);

    setAuthSession(session!);
    expect(getAccessToken()).toBe(token);
    expect(getAuthSession()?.membershipId).toBe("mem-42");
    expect(document.cookie).toContain(`${AUTH_COOKIE_NAME}=`);
    expect(eventsFired.length).toBeGreaterThanOrEqual(1);

    clearAuthSession();
    expect(getAccessToken()).toBeNull();
    expect(getAuthSession()).toBeNull();
    window.removeEventListener(AUTH_CHANGED_EVENT, listener);
  });

  it("ignores expired JWT sessions on getAuthSession()", () => {
    const expiredToken = encodeFakeJwt({
      sub: "user-old",
      tenant_id: "tenant-old",
      roles: ["STUDENT"],
      exp: Math.floor(Date.now() / 1000) - 60,
    });
    const session = buildSessionFromLoginResponse({ access_token: expiredToken });
    expect(session).not.toBeNull();
    setAuthSession(session!);
    expect(getAuthSession()).toBeNull();
  });

  it("sanitizes unsafe open-redirect next targets", () => {
    expect(sanitizeRedirectTarget("/phong-thi/att-1?foo=bar")).toBe("/phong-thi/att-1?foo=bar");
    expect(sanitizeRedirectTarget("https://evil.example/steal")).toBe("/");
    expect(sanitizeRedirectTarget("//evil.example")).toBe("/");
    expect(sanitizeRedirectTarget(null)).toBe("/");
  });

  it("parses RFC 7807 Problem+JSON payloads cleanly for 403/409/429", async () => {
    const response = new Response(
      JSON.stringify({
        type: "https://h2edu.vn/errors/rate-limited",
        title: "Too Many Requests",
        status: 429,
        detail: "Bạn thao tác quá nhanh, vui lòng thử lại sau 10 giây.",
        error_code: "RATE_LIMIT_EXCEEDED",
        correlation_id: "corr-999",
      }),
      {
        status: 429,
        headers: { "Content-Type": "application/problem+json" },
      },
    );

    const parsed = await parseProblemDetail(response);
    expect(parsed.status).toBe(429);
    expect(parsed.error_code).toBe("RATE_LIMIT_EXCEEDED");
    expect(parsed.correlation_id).toBe("corr-999");

    const formatted = formatProblemMessage(parsed);
    expect(formatted).toContain("[429 Too Many Requests]");
    expect(formatted).toContain("RATE_LIMIT_EXCEEDED");
    expect(formatted).toContain("corr-999");
  });

  it("injects Authorization Bearer token on apiFetch and redirects on 401", async () => {
    const token = encodeFakeJwt({
      sub: "teacher-1",
      tenant_id: "tenant-1",
      roles: ["TEACHER"],
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    setAuthSession(buildSessionFromRawToken(token)!);

    const fetchMock = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) => {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    await apiFetch("/api/v1/classes/c1", {
      fetchFn: fetchMock as unknown as typeof fetch,
      method: "POST",
      json: { hello: "world" },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/classes/c1",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        }),
      }),
    );

    // 401 triggers clearAuthSession + redirect to /dang-nhap
    const redirects: string[] = [];
    setAuthNavigationHandler((url) => redirects.push(url));

    const unauthorizedFetch = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          title: "Unauthorized",
          status: 401,
          detail: "Token đã hết hạn.",
          error_code: "TOKEN_EXPIRED",
        }),
        { status: 401, headers: { "Content-Type": "application/problem+json" } },
      );
    });

    await expect(
      apiFetch("/api/v1/protected", { fetchFn: unauthorizedFetch as unknown as typeof fetch }),
    ).rejects.toThrow(ApiRequestError);

    expect(getAuthSession()).toBeNull();
    expect(redirects[0]).toMatch(/^\/dang-nhap/);
  });

  it("maps SanitizedExamPaperContract and SyncResponse into Workspace 2 state", () => {
    const mapped = mapPaperPayloadToExamAttempt(
      {
        attempt_id: "att-live-100",
        assessment_version_id: "av-1",
        deadline_at: "2026-12-01T10:00:00Z",
        questions: [
          {
            question_version_id: "qv-3",
            section_code: "PART_3",
            item_type: "SHORT_NUMERIC",
            display_position: 3,
            stem_json: { markdown: "Tính $2^{5}$" },
            options: [],
          },
          {
            question_version_id: "qv-1",
            section_code: "PART_1",
            item_type: "SINGLE_CHOICE",
            display_position: 1,
            stem_json: { markdown: "Chọn giá trị $1+1$" },
            options: [
              { display_label: "A", content_json: { text_md: "2" } },
              { display_label: "B", content_json: { text_md: "3" } },
            ],
          },
          {
            question_version_id: "qv-2",
            section_code: "PART_2",
            item_type: "CLUSTER_TRUE_FALSE",
            display_position: 2,
            stimulus_json: { markdown: "Đoạn dẫn hình học" },
            stem_json: { markdown: "Xét 4 mệnh đề" },
            options: [
              { display_label: "a", content_json: { text: "Mệnh đề a" } },
              { display_label: "b", content_json: { text: "Mệnh đề b" } },
            ],
          },
        ],
      },
      "fallback-id",
    );

    expect(mapped.attemptId).toBe("att-live-100");
    expect(mapped.questions.map((q) => q.questionVersionId)).toEqual(["qv-1", "qv-2", "qv-3"]);
    expect(mapped.questions[0]!.part).toBe("PART_1");
    expect(mapped.questions[1]!.part).toBe("PART_2");
    expect(mapped.questions[1]!.stem).toContain("Đoạn dẫn hình học");
    expect(mapped.questions[2]!.part).toBe("PART_3");

    const hydrated = hydrateAnswersFromSyncPayload(
      mapped.questions,
      {
        responses: {
          "qv-1": {
            response_payload: { choice: "A", eliminated: ["B"] },
            is_flagged: true,
          },
          "qv-3": {
            response_payload: { short_text: "32" },
            is_flagged: false,
          },
        },
      },
      (question, prev, payload) => {
        const obj = (payload ?? {}) as Record<string, unknown>;
        if (question.part === "PART_1") {
          return {
            ...createEmptyAnswerState(),
            ...prev,
            choice: obj.choice === "A" ? "A" : null,
            eliminated: Array.isArray(obj.eliminated) ? ["B"] : [],
          };
        }
        return { ...createEmptyAnswerState(), shortText: String(obj.short_text ?? "") };
      },
    );

    expect(hydrated["qv-1"]?.choice).toBe("A");
    expect(hydrated["qv-1"]?.flagged).toBe(true);
    expect(hydrated["qv-3"]?.shortText).toBe("32");
  });
});

describe("DEMO_ACCOUNTS match the accounts of `python -m app.ops.seed_demo`", () => {
  it("quick-fills the four seeded e-mails, roles, tenant slug and password", () => {
    expect(DEMO_PASSWORD).toBe("Demo@12345");
    expect(DEMO_TENANT_SLUG).toBe("h2edu-demo");
    expect(DEMO_ACCOUNTS.map((a) => [a.id, a.email, a.fallbackSession.roles])).toEqual([
      ["learner", "learner@h2edu.demo", ["LEARNER"]],
      ["teacher", "teacher@h2edu.demo", ["TEACHER"]],
      ["tenant_admin", "tenant_admin@h2edu.demo", ["TENANT_ADMIN"]],
      ["platform_admin", "platform_admin@h2edu.demo", ["PLATFORM_ADMIN"]],
    ]);
    for (const account of DEMO_ACCOUNTS) {
      expect(account.password).toBe(DEMO_PASSWORD);
      expect(account.tenantSlug).toBe(DEMO_TENANT_SLUG);
      expect(account.fallbackSession.email).toBe(account.email);
    }
  });
});

describe("parseProblemDetail FastAPI validation arrays", () => {
  it("formats Pydantic 422 validation error arrays instead of hiding field details", async () => {
    const response = new Response(
      JSON.stringify({
        detail: [
          { loc: ["body", "stem_text"], msg: "Field required", type: "missing" },
          { loc: ["path", "class_id"], msg: "Input should be a valid UUID", type: "uuid_parsing" },
        ],
      }),
      { status: 422, statusText: "Unprocessable Entity" },
    );
    const parsed = await parseProblemDetail(response);
    expect(parsed.status).toBe(422);
    expect(parsed.detail).toBe("stem_text: Field required; class_id: Input should be a valid UUID");
  });
});
