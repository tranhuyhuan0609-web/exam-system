// @vitest-environment jsdom

import "fake-indexeddb/auto";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveWorkspacePanel } from "@/components/auth/LiveWorkspacePanel";
import { mapGradebookRows, TeacherClassLmsShell } from "@/components/teacher-lms/TeacherClassLmsShell";
import { buildSessionFromRawToken, clearAuthSession, setAuthSession } from "@/lib/auth-client";

const CLASS_ID = "01a1df80-8dac-7cb1-9b1d-4b466d142433";
const ASSIGNMENT_ID = "01a1df86-5c11-7a00-8f00-000000000001";
const ASSESSMENT_ID = "01a1df86-2472-7ab3-b8ad-bdb6a5975210";
const VERSION_ID = "01a1df86-2498-7cf2-903a-c966ec3f6d92";
const ATTEMPT_ID = "01a1df88-1111-7222-8333-444455556666";
const MEMBERSHIP_ID = "01a1df80-8c05-71b6-9f25-623e0d1a0148";

function fakeJwt(roles: string[]): string {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const body = Buffer.from(
    JSON.stringify({ sub: "u-1", tenant_id: "t-1", membership_id: MEMBERSHIP_ID, roles }),
    "utf-8",
  ).toString("base64url");
  return `${header}.${body}.sig`;
}

function signIn(roles: string[]): void {
  setAuthSession(buildSessionFromRawToken(fakeJwt(roles))!);
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const classes = [{ id: CLASS_ID, name: "Lớp 12A1", invite_code: "class-12a1", active_student_count: 1 }];
const assignments = [
  {
    id: ASSIGNMENT_ID,
    class_id: CLASS_ID,
    assessment_id: ASSESSMENT_ID,
    assessment_version_id: VERSION_ID,
    assessment_title: "Đề thi thử Toán THPT 2025 (demo)",
    due_at: null,
    max_attempts: 3,
  },
];
const papers = [{ id: ASSESSMENT_ID, title: "Đề thi thử Toán THPT 2025 (demo)", subject_code: "math", current_version_id: VERSION_ID }];

afterEach(() => {
  cleanup();
  clearAuthSession();
  window.history.replaceState({}, "", "/");
});

describe("LiveWorkspacePanel: real UUIDs from the backend on the home page", () => {
  it("renders nothing without a session, so the static demo links stay the only way in", () => {
    const fetchMock = vi.fn();
    const { container } = render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} />);
    expect(container.innerHTML).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders nothing on a ?demo=1 page even when signed in", () => {
    signIn(["LEARNER"]);
    window.history.replaceState({}, "", "/?demo=1");
    const fetchMock = vi.fn();
    const { container } = render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} />);
    expect(container.innerHTML).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lets a learner start the class assignment with its real ids and opens the new attempt", async () => {
    signIn(["LEARNER"]);
    const onOpenAttempt = vi.fn();
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url.endsWith("/api/v1/classes")) return json(classes);
      if (url.endsWith("/api/v1/assignments")) return json(assignments);
      if (url.endsWith("/api/v1/assessments")) return json(papers);
      if (url.endsWith("/api/v1/attempts")) return json([]);
      if (url.endsWith("/api/v1/exam-sessions/start")) return json({ attempt_id: ATTEMPT_ID, resumed: false }, 201);
      return json({}, 404);
    });

    render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} onOpenAttempt={onOpenAttempt} />);

    const startBtn = await screen.findByTestId(`live-start-assignment-${ASSIGNMENT_ID}`);
    expect(screen.queryByTestId(`live-class-${CLASS_ID}`)).toBeNull(); // a learner has no teacher page to open
    fireEvent.click(startBtn);

    await waitFor(() => expect(onOpenAttempt).toHaveBeenCalledWith(ATTEMPT_ID));
    const start = calls.find((c) => c.url.endsWith("/exam-sessions/start"));
    expect(start?.method).toBe("POST");
    expect(start?.body).toEqual({ assessment_id: ASSESSMENT_ID, assignment_id: ASSIGNMENT_ID });
  });

  it("starts open practice on a published paper without an assignment id", async () => {
    signIn(["LEARNER"]);
    const onOpenAttempt = vi.fn();
    let startBody: unknown;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/assessments")) return json(papers);
      if (url.endsWith("/exam-sessions/start")) {
        startBody = JSON.parse(String(init?.body));
        return json({ attempt_id: ATTEMPT_ID }, 200);
      }
      return json([]);
    });
    render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} onOpenAttempt={onOpenAttempt} />);
    fireEvent.click(await screen.findByTestId(`live-start-paper-${ASSESSMENT_ID}`));
    await waitFor(() => expect(onOpenAttempt).toHaveBeenCalledWith(ATTEMPT_ID));
    expect(startBody).toEqual({ assessment_id: ASSESSMENT_ID });
  });

  it("shows an open attempt as a link to its room and surfaces a refused start", async () => {
    signIn(["LEARNER"]);
    const onOpenAttempt = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/assignments")) return json(assignments);
      if (url.endsWith("/api/v1/attempts"))
        return json([
          {
            attempt_id: ATTEMPT_ID,
            assessment_title: "Đề thi thử Toán THPT 2025 (demo)",
            assignment_id: ASSIGNMENT_ID,
            status: "IN_PROGRESS",
            deadline_at: new Date(Date.now() + 3_600_000).toISOString(),
          },
        ]);
      if (url.endsWith("/exam-sessions/start"))
        return json({ title: "Forbidden", status: 403, detail: "Assignment due_at has passed", reason: "ASSIGNMENT_PAST_DUE" }, 403);
      return json([]);
    });
    render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} onOpenAttempt={onOpenAttempt} />);

    const room = await screen.findByTestId(`live-attempt-${ATTEMPT_ID}`);
    expect(room.getAttribute("href")).toBe(`/phong-thi/${ATTEMPT_ID}`);

    fireEvent.click(await screen.findByTestId(`live-start-assignment-${ASSIGNMENT_ID}`));
    const alert = await screen.findByTestId("live-workspace-error");
    expect(alert.textContent).toContain("due_at");
    expect(onOpenAttempt).not.toHaveBeenCalled();
  });

  it("links a teacher to the class page by its UUID and offers no start button", async () => {
    signIn(["TEACHER"]);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/classes")) return json(classes);
      if (url.endsWith("/api/v1/assignments")) return json(assignments);
      return json([]);
    });
    render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} />);
    const link = await screen.findByTestId(`live-class-${CLASS_ID}`);
    expect(link.getAttribute("href")).toBe(`/teacher/classes/${CLASS_ID}`);
    expect(screen.queryByTestId(`live-start-assignment-${ASSIGNMENT_ID}`)).toBeNull();
  });

  it("ignores rows whose id is not a UUID", async () => {
    signIn(["TEACHER"]);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/api/v1/classes")
        ? json([{ id: "class-12a1", name: "Mẫu", invite_code: null, active_student_count: 0 }])
        : json([]),
    );
    render(<LiveWorkspacePanel fetchFn={fetchMock as unknown as typeof fetch} />);
    expect(await screen.findByTestId("live-workspace-empty")).toBeTruthy();
    expect(screen.queryByTestId("live-classes")).toBeNull();
  });
});

describe("TeacherClassLmsShell: the class page on the real class UUID", () => {
  it("maps gradebook rows: a row without submitted_at is unsubmitted, a late one is flagged", () => {
    const rows = mapGradebookRows([
      { membership_id: "m-1", student_label: "Minh", score: "0.00", submitted_at: null, due_at: null },
      {
        membership_id: "m-2",
        student_label: "Lan",
        score: "7.25",
        submitted_at: "2026-11-15T10:00:00Z",
        due_at: "2026-11-15T09:00:00Z",
      },
    ]);
    expect(rows[0]).toMatchObject({ submitted: false, score: null, isLate: false, studentName: "Minh" });
    expect(rows[1]).toMatchObject({ submitted: true, score: 7.25, isLate: true });
  });

  it("loads the class, its assignment and gradebook, exports it, and hands out another paper with real ids", async () => {
    signIn(["TEACHER"]);
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === `/api/v1/classes/${CLASS_ID}`)
        return json({
          classroom: { id: CLASS_ID, name: "Lớp 12A1", invite_code: "class-12a1" },
          members: [{ membership_id: MEMBERSHIP_ID, role_in_class: "STUDENT", status: "ACTIVE" }],
          active_student_count: 1,
        });
      if (url === "/api/v1/assessments") return json(papers);
      if (url === `/api/v1/assignments?class_id=${CLASS_ID}`) return json(assignments);
      if (url.startsWith(`/api/v1/classes/${CLASS_ID}/gradebook/export`))
        return new Response("student_code,score\nabc,7.25", { status: 200, headers: { "Content-Type": "text/csv" } });
      if (url.startsWith(`/api/v1/classes/${CLASS_ID}/gradebook`))
        return json({
          rows: [
            {
              membership_id: MEMBERSHIP_ID,
              student_label: "Minh",
              score: "7.25",
              submitted_at: "2026-11-15T10:00:00Z",
              due_at: null,
            },
          ],
        });
      if (url === "/api/v1/assignments" && method === "POST") return json({ id: ASSIGNMENT_ID }, 201);
      return json({}, 404);
    });

    render(<TeacherClassLmsShell classId={CLASS_ID} fetchFn={fetchMock as unknown as typeof fetch} />);

    // the class, the assignment of the class and the gradebook of that assignment are all read by UUID
    await waitFor(() =>
      expect(calls.some((c) => c.url === `/api/v1/classes/${CLASS_ID}/gradebook?assignment_id=${ASSIGNMENT_ID}`)).toBe(true),
    );
    expect(await screen.findByText(/Đề thi thử Toán THPT 2025 \(demo\)/, { selector: "h1" })).toBeTruthy();

    // exporting the gradebook uses the real class and assignment ids
    fireEvent.click(await screen.findByTestId("export-gradebook-csv-btn"));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url === `/api/v1/classes/${CLASS_ID}/gradebook/export?assignment_id=${ASSIGNMENT_ID}`,
        ),
      ).toBe(true),
    );

    // assigning a published paper posts class_id and assessment_version_id, both real UUIDs
    fireEvent.click(await screen.findByTestId("assign-paper-btn"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/assignments" && c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.url === "/api/v1/assignments" && c.method === "POST")?.body).toEqual({
      class_id: CLASS_ID,
      assessment_version_id: VERSION_ID,
    });
  });

  it("keeps the local demo class for a non-UUID id and never calls the backend for it", async () => {
    signIn(["TEACHER"]);
    const fetchMock = vi.fn(async () => json({}, 404));
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<TeacherClassLmsShell classId="class-12a1" />);
      expect(await screen.findByTestId("export-gradebook-csv-btn")).toBeTruthy();
      fireEvent.click(screen.getByTestId("export-gradebook-csv-btn"));
      await screen.findByText(/cục bộ/);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
