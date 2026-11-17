// @vitest-environment jsdom
// A real teacher session shows the school's own data and no sample data; sample data needs `?demo=1`.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MatrixExamBuilderShell } from "@/components/teacher-lms/MatrixExamBuilderShell";
import { mapItemAnalysis, TeacherClassLmsShell } from "@/components/teacher-lms/TeacherClassLmsShell";
import { buildSessionFromRawToken, clearAuthSession, setAuthSession } from "@/lib/auth-client";

const CLASS_ID = "0192a8f0-0000-7000-8000-0000000000c1";

function signIn(): void {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const body = Buffer.from(
    JSON.stringify({ sub: "gv-1", tenant_id: "t-1", roles: ["TEACHER"], name: "Cô Lan" }),
    "utf-8",
  ).toString("base64url");
  setAuthSession(buildSessionFromRawToken(`${header}.${body}.sig`)!);
}

function stubApi(routes: Record<string, unknown>): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const hit = Object.entries(routes).find(([path]) => url.includes(path));
    return new Response(JSON.stringify(hit ? hit[1] : []), {
      status: hit ? 200 : 404,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  clearAuthSession();
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("signed-out teacher", () => {
  it("is sent to the login page instead of seeing sample data", async () => {
    render(<MatrixExamBuilderShell />);
    expect(await screen.findByTestId("builder-signed-out")).toBeTruthy();
    expect(screen.queryByText(/Xác suất có điều kiện/)).toBeNull();
    cleanup();

    render(<TeacherClassLmsShell classId={CLASS_ID} />);
    expect(await screen.findByTestId("class-signed-out")).toBeTruthy();
    expect(screen.queryByText(/Nguyễn Minh Anh/)).toBeNull();
  });

  it("sees sample data only with ?demo=1", async () => {
    window.history.pushState({}, "", "/?demo=1");
    render(<TeacherClassLmsShell classId={CLASS_ID} />);
    expect(await screen.findByTestId("gradebook-table-panel")).toBeTruthy();
    expect(screen.getAllByText(/Nguyễn Minh Anh/).length).toBeGreaterThan(0);
  });
});

describe("signed-in teacher with an empty school", () => {
  it("builder: no sample topics, an empty-bank notice, and the PDF tab is still there", async () => {
    signIn();
    stubApi({ "/assessments/pool-summary": [], "/assessments": [], "/classes": [], "/taxonomy/nodes": { items: [] } });
    render(<MatrixExamBuilderShell />);
    expect(await screen.findByTestId("empty-question-bank-notice")).toBeTruthy();
    expect(screen.getByTestId("tab-from-pdf")).toBeTruthy();
    expect(screen.queryByText(/Ứng dụng đạo hàm/)).toBeNull();
    expect(screen.queryByText(/Bayes/)).toBeNull();
  });

  it("builder: the live topics of the question bank are the matrix rows", async () => {
    signIn();
    const nodeId = "0192a8f0-0000-7000-8000-0000000000d1";
    stubApi({
      "/assessments/pool-summary": [
        { taxonomy_node_id: nodeId, code_path: "TOAN12.HS", item_type: "SINGLE_CHOICE", difficulty_level: "NB", available: 9 },
      ],
      "/assessments": [],
      "/classes": [],
      "/taxonomy/nodes": { items: [{ id: nodeId, code: "HS", name: "Hàm số", code_path: "TOAN12.HS" }] },
    });
    render(<MatrixExamBuilderShell />);
    await waitFor(() => expect(screen.getByTestId(`matrix-cell-${nodeId}-NB-SINGLE_CHOICE`)).toBeTruthy());
    expect(screen.getByText("Hàm số")).toBeTruthy();
    expect(screen.queryByTestId("empty-question-bank-notice")).toBeNull();
  });

  it("class: shows its own name, invite code and no invented students or analysis", async () => {
    signIn();
    stubApi({
      [`/api/v1/classes/${CLASS_ID}`]: {
        classroom: { id: CLASS_ID, name: "Lớp 12A1 thật", invite_code: "ABC123" },
        members: [],
      },
    });
    render(<TeacherClassLmsShell classId={CLASS_ID} />);
    expect(await screen.findByTestId("class-no-students-notice")).toBeTruthy();
    expect(screen.getByText(/Lớp 12A1 thật/)).toBeTruthy();
    expect(screen.getAllByText(/ABC123/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Nguyễn Minh Anh/)).toBeNull();
    expect(screen.queryByTestId("teacher-copilot-panel")).toBeNull();
    expect(screen.queryByTestId("item-analysis-panel")).toBeNull();
  });
});

describe("mapItemAnalysis", () => {
  it("turns the API's hardest questions into cards", () => {
    expect(
      mapItemAnalysis([
        { question_id: "abcdef0123", attempt_count: 8, wrong_count: 6, error_rate: "0.7500", top_wrong_distractor: "c" },
        { question_id: "zzz", attempt_count: 2, wrong_count: 1, error_rate: "0.5000", top_wrong_distractor: null },
      ]),
    ).toMatchObject([
      { stableCode: "abcdef01", wrongRatePct: 75, topDistractorKey: "C", totalResponses: 8, distractors: [] },
      { stableCode: "zzz", wrongRatePct: 50, topDistractorKey: "A" },
    ]);
  });
});
