// @vitest-environment jsdom

import "fake-indexeddb/auto";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { AdminOpsConsoleShell } from "@/components/admin-ops/AdminOpsConsoleShell";
import { LoginShell } from "@/components/auth/LoginShell";
import { ExamRoomShell } from "@/components/exam-room/ExamRoomShell";
import { BboxReviewerShell } from "@/components/studio-review/BboxReviewerShell";
import { StudyHubShell } from "@/components/study-hub/StudyHubShell";
import { TeacherClassLmsShell } from "@/components/teacher-lms/TeacherClassLmsShell";
import {
  buildSessionFromRawToken,
  clearAuthSession,
  getAuthSession,
  setAuthSession,
} from "@/lib/auth-client";

let mockSearchParams = new URLSearchParams();
const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => mockSearchParams,
}));

function encodeFakeJwt(payload: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const body = Buffer.from(JSON.stringify(payload), "utf-8").toString("base64url");
  return `${header}.${body}.sig`;
}

afterEach(() => {
  cleanup();
  clearAuthSession();
  mockSearchParams = new URLSearchParams();
  mockPush.mockReset();
});

describe("LoginShell (/dang-nhap) & AuthStatusBadge E2E", () => {
  it("submits credentials to POST /api/v1/auth/login, stores AuthSession metadata, and redirects to ?next", async () => {
    mockSearchParams = new URLSearchParams("next=/phong-thi/att-99");
    const jwt = encodeFakeJwt({
      sub: "user-hs-1",
      tenant_id: "tenant-thpt-01",
      membership_id: "mem-hs-1",
      roles: ["STUDENT"],
      entitlement_tier: "STUDENT_PRO",
    });

    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          access_token: jwt,
          roles: ["STUDENT"],
          tenant_id: "tenant-thpt-01",
          membership_id: "mem-hs-1",
          entitlement_tier: "STUDENT_PRO",
          display_name: "Nguyễn Văn A",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    render(<LoginShell fetchFn={fetchMock as unknown as typeof fetch} />);

    fireEvent.change(screen.getByTestId("login-email-input"), {
      target: { value: "hs@h2edu.vn" },
    });
    fireEvent.change(screen.getByTestId("login-password-input"), {
      target: { value: "SecretPass123!" },
    });
    fireEvent.change(screen.getByTestId("login-tenant-input"), {
      target: { value: "thpt-01" },
    });
    fireEvent.click(screen.getByTestId("login-submit-btn"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/login",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            email: "hs@h2edu.vn",
            password: "SecretPass123!",
            tenant_slug: "thpt-01",
          }),
        }),
      );
    });

    const stored = getAuthSession();
    expect(stored?.accessToken).toBe(jwt);
    expect(stored?.tenantId).toBe("tenant-thpt-01");
    expect(stored?.roles).toEqual(["STUDENT"]);
    expect(mockPush).toHaveBeenCalledWith("/phong-thi/att-99");
  });

  it("shows clear guidance + expands Dev JWT paste panel when backend /auth/login returns 404", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(
        JSON.stringify({ title: "Not Found", status: 404, detail: "Route not registered" }),
        { status: 404, headers: { "Content-Type": "application/problem+json" } },
      );
    });

    render(<LoginShell fetchFn={fetchMock as unknown as typeof fetch} demoMode />);
    fireEvent.change(screen.getByTestId("login-email-input"), {
      target: { value: "gv@h2edu.vn" },
    });
    fireEvent.change(screen.getByTestId("login-password-input"), {
      target: { value: "Pass123!" },
    });
    fireEvent.click(screen.getByTestId("login-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("login-error-banner").textContent).toContain("POST /api/v1/auth/login");
    });
    expect(screen.getByTestId("raw-jwt-input")).toBeTruthy();

    const rawToken = encodeFakeJwt({
      sub: "dev-teacher",
      tenant_id: "tenant-dev",
      roles: ["TEACHER"],
    });
    fireEvent.change(screen.getByTestId("raw-jwt-input"), { target: { value: rawToken } });
    fireEvent.click(screen.getByTestId("raw-jwt-submit-btn"));

    expect(getAuthSession()?.roles).toEqual(["TEACHER"]);
    expect(mockPush).toHaveBeenCalledWith("/");
  });

  it("supports Demo Quick-Fill & HomePage AuthStatusBadge logout", async () => {
    render(<LoginShell demoMode />);
    expect(screen.getByTestId("demo-accounts-panel")).toBeTruthy();
    fireEvent.click(screen.getByTestId("demo-fill-teacher"));
    expect((screen.getByTestId("login-email-input") as HTMLInputElement).value).toBe(
      "giaovien.demo@h2edu.vn",
    );

    cleanup();
    const token = encodeFakeJwt({
      sub: "admin-1",
      tenant_id: "tenant-ops",
      roles: ["ADMIN"],
      name: "Admin H2Edu",
    });
    setAuthSession(buildSessionFromRawToken(token)!);

    render(<HomePage />);
    expect(screen.getByTestId("auth-session-pill").textContent).toContain("ADMIN");
    expect(screen.getByTestId("auth-session-pill").textContent).toContain("tenant-ops");

    fireEvent.click(screen.getByTestId("auth-logout-btn"));
    expect(getAuthSession()).toBeNull();
    expect(screen.getByTestId("auth-login-link")).toBeTruthy();
  });
});

describe("Workspaces Live Bearer Injection & RFC 7807 Problem+JSON Error Handling", () => {
  it("loads WS2 paper from GET /api/v1/attempts/{id}/paper + /sync with Bearer token", async () => {
    const token = encodeFakeJwt({ sub: "hs-1", tenant_id: "t-1", roles: ["STUDENT"] });
    setAuthSession(buildSessionFromRawToken(token)!);

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/time")) {
        return new Response(
          JSON.stringify({ server_time: new Date().toISOString() }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/paper")) {
        return new Response(
          JSON.stringify({
            attempt_id: "att-live-77",
            assessment_version_id: "av-1",
            deadline_at: new Date(Date.now() + 45 * 60 * 1000).toISOString(),
            questions: [
              {
                question_version_id: "qv-live-1",
                section_code: "PART_1",
                item_type: "SINGLE_CHOICE",
                display_position: 1,
                stem_json: { markdown: "Giá trị của $\\log_2 8$ bằng bao nhiêu?" },
                options: [
                  { display_label: "A", content_json: { text_md: "2" } },
                  { display_label: "B", content_json: { text_md: "3" } },
                ],
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/sync")) {
        return new Response(
          JSON.stringify({
            attempt_id: "att-live-77",
            status: "IN_PROGRESS",
            responses: {
              "qv-live-1": { response_payload: { choice: "B" }, is_flagged: true },
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("{}", { status: 200 });
    });

    render(<ExamRoomShell attemptId="att-live-77" fetchFn={fetchMock as unknown as typeof fetch} />);

    await waitFor(() => {
      expect(screen.getByTestId("exam-question-palette")).toBeTruthy();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/attempts/att-live-77/paper",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
      }),
    );
    const palette = screen.getByTestId("exam-question-palette");
    expect(palette.textContent).toContain("1");
  });

  it("displays RFC 7807 Problem+JSON error banners on WS1, WS3, WS5, and WS6 instead of silent success", async () => {
    const token = encodeFakeJwt({ sub: "ops-1", tenant_id: "t-1", roles: ["ADMIN", "TEACHER"] });
    setAuthSession(buildSessionFromRawToken(token)!);

    // 1) WS5 TeacherClassLmsShell 403 Forbidden
    const forbiddenFetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/gradebook/export")) {
        return new Response(
          JSON.stringify({
            title: "Forbidden",
            status: 403,
            detail: "Không có quyền xuất CSV lớp của tenant khác.",
            error_code: "CROSS_TENANT_FORBIDDEN",
            correlation_id: "trace-403",
          }),
          { status: 403, headers: { "Content-Type": "application/problem+json" } },
        );
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    render(<TeacherClassLmsShell classId="class-12a1" fetchFn={forbiddenFetch as unknown as typeof fetch} />);
    fireEvent.click(screen.getByTestId("export-gradebook-csv-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-api-error").textContent).toContain("CROSS_TENANT_FORBIDDEN");
    });
    expect(forbiddenFetch).toHaveBeenCalledWith(
      "/api/v1/classes/class-12a1/gradebook/export",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
      }),
    );
    cleanup();

    // 2) WS6 AdminOpsConsoleShell 429 Rate Limit on Feature Flag PATCH
    const rateLimitFetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return new Response(
          JSON.stringify({
            title: "Too Many Requests",
            status: 429,
            detail: "Vượt ngưỡng bật tắt feature flag.",
            error_code: "RATE_LIMIT",
          }),
          { status: 429, headers: { "Content-Type": "application/problem+json" } },
        );
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    });

    render(<AdminOpsConsoleShell fetchFn={rateLimitFetch as unknown as typeof fetch} />);
    fireEvent.click(screen.getByTestId("toggle-flag-btn-parser.vlm_table_fallback"));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-api-error").textContent).toContain("[429 Too Many Requests]");
    });
    expect(rateLimitFetch).toHaveBeenCalledWith(
      "/api/v1/admin/feature-flags/parser.vlm_table_fallback",
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ Authorization: `Bearer ${token}` }),
        body: JSON.stringify({ is_enabled: false }),
      }),
    );
    cleanup();

    // 3) WS1 StudyHubShell + AiTutorErrorDrawer Problem+JSON 403 EXAM_SESSION_ACTIVE
    const ws1Fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/tutor/sessions")) {
        return new Response(
          JSON.stringify({
            title: "Forbidden",
            status: 403,
            detail: "Học sinh đang làm bài thi nên khóa AI Tutor.",
            error_code: "EXAM_SESSION_ACTIVE",
          }),
          { status: 403, headers: { "Content-Type": "application/problem+json" } },
        );
      }
      if (url.includes("/error-notes")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    render(<StudyHubShell slug="cong-thuc-bayes" fetchFn={ws1Fetch as unknown as typeof fetch} />);
    fireEvent.change(screen.getByTestId("tutor-question-input"), {
      target: { value: "Gợi ý bước 1" },
    });
    fireEvent.click(screen.getByTestId("tutor-send-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-api-error").textContent).toContain("EXAM_SESSION_ACTIVE");
    });
    cleanup();

    // 4) WS3 BboxReviewerShell 409 Conflict
    const ws3Fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Response(
          JSON.stringify({
            title: "Conflict",
            status: 409,
            detail: "Bản ghi đã được duyệt trước đó.",
            error_code: "REVIEW_CONFLICT",
          }),
          { status: 409, headers: { "Content-Type": "application/problem+json" } },
        );
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    });

    render(<BboxReviewerShell runId="run-conflict" fetchFn={ws3Fetch as unknown as typeof fetch} />);
    fireEvent.change(screen.getByTestId("ws3-source-answer-input"), { target: { value: "0,33" } });
    fireEvent.click(screen.getByTestId("ws3-mark-verified-btn"));
    fireEvent.change(screen.getByTestId("ws3-rights-select"), { target: { value: "OWNED" } });
    fireEvent.click(screen.getByTestId("ws3-publish-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-api-error").textContent).toContain("REVIEW_CONFLICT");
    });
  });
});
