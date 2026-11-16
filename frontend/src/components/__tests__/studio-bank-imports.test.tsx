// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { LiveWorkspacePanel } from "@/components/auth/LiveWorkspacePanel";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { QuestionBankShell } from "@/components/studio-bank/QuestionBankShell";
import { ImportsHubShell } from "@/components/studio-imports/ImportsHubShell";
import { MatrixExamBuilderShell } from "@/components/teacher-lms/MatrixExamBuilderShell";
import { TeacherHeader } from "@/components/teacher-lms/TeacherHeader";
import { buildSessionFromRawToken, clearAuthSession, setAuthSession } from "@/lib/auth-client";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function encodeFakeJwt(payload: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const body = Buffer.from(JSON.stringify(payload), "utf-8").toString("base64url");
  return `${header}.${body}.sig`;
}

function signInTeacher(roles: string[] = ["TEACHER", "EDITOR"]) {
  const jwt = encodeFakeJwt({
    sub: "11111111-1111-4111-8111-111111111111",
    tenant_id: "22222222-2222-4222-8222-222222222222",
    membership_id: "33333333-3333-4333-8333-333333333333",
    roles,
    entitlement_tier: "TEACHER_PRO",
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  setAuthSession(buildSessionFromRawToken(jwt)!);
}

afterEach(() => {
  cleanup();
  clearAuthSession();
});

describe("QuestionBankShell (/studio/questions)", () => {
  it("prompts for sign-in when unauthenticated and does not hit protected endpoints", () => {
    const fetchMock = vi.fn();
    render(<QuestionBankShell fetchFn={fetchMock as unknown as typeof fetch} />);
    expect(screen.getByTestId("question-bank-auth-prompt")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("hydrates taxonomy + questions, supports filters, cursor pagination, detail inspection, and draft version fork", async () => {
    signInTeacher();
    const q1Id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
    const q2Id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2";
    const forkCreatedCalls: unknown[] = [];

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.startsWith("/api/v1/taxonomy/nodes")) {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
                code: "TOAN12_BAYES",
                name: "Xác suất Bayes",
                node_type: "TOPIC",
                code_path: "TOAN.12.BAYES",
              },
            ],
            next_cursor: null,
            has_more: false,
            limit: 100,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.startsWith(`/api/v1/questions/${q1Id}/versions`) && method === "POST") {
        forkCreatedCalls.push(JSON.parse(String(init?.body ?? "{}")));
        return new Response(
          JSON.stringify({
            id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc2",
            question_id: q1Id,
            version_no: 2,
            stem: { text: "Nội dung đã cập nhật v2" },
            options: [{ key: "A", text: "1/2" }],
            is_immutable: false,
            content_hash: "hash-v2",
            change_reason: "Cập nhật diễn đạt thử",
            created_at: "2026-11-16T00:00:00Z",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url === `/api/v1/questions/${q1Id}` && method === "GET") {
        return new Response(
          JSON.stringify({
            question: {
              id: q1Id,
              tenant_id: "22222222-2222-4222-8222-222222222222",
              visibility_scope: "TENANT_PRIVATE",
              stable_code: "C01_SAMPLE_MCQ_1",
              item_type: "SINGLE_CHOICE",
              primary_taxonomy_node_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
              curriculum_node_id: null,
              exam_framework_tags: ["THPTQG_2025"],
              difficulty_level: "TH",
              current_version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
              acquisition_status: "AUTHORED",
              verification_status: "UNVERIFIED",
              rights_status: "OWNED",
              publication_status: "DRAFT",
              access_tier: "FREE",
              created_at: "2026-11-15T00:00:00Z",
            },
            shown_version: {
              id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
              question_id: q1Id,
              version_no: 1,
              stem: { text: "Tính xác suất có điều kiện $P(A|B)$" },
              options: [
                { key: "A", text: "0.7" },
                { key: "B", text: "0.3" },
              ],
              is_immutable: false,
              content_hash: "hash-v1",
              change_reason: "Khởi tạo",
              created_at: "2026-11-15T00:00:00Z",
            },
            versions: [
              {
                id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
                version_no: 1,
                is_immutable: false,
                is_current: true,
                change_reason: "Khởi tạo",
                created_by: null,
                created_at: "2026-11-15T00:00:00Z",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.startsWith("/api/v1/questions?") && url.includes("cursor=cur-page-2")) {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: q2Id,
                tenant_id: "22222222-2222-4222-8222-222222222222",
                visibility_scope: "TENANT_PRIVATE",
                stable_code: "C01_SAMPLE_ESSAY_2",
                item_type: "ESSAY",
                primary_taxonomy_node_id: null,
                curriculum_node_id: null,
                exam_framework_tags: [],
                difficulty_level: "VD",
                current_version_id: null,
                acquisition_status: "AUTHORED",
                verification_status: "UNVERIFIED",
                rights_status: "OWNED",
                publication_status: "DRAFT",
                access_tier: "FREE",
                created_at: "2026-11-15T00:00:00Z",
                version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc9",
                version_no: 1,
                is_immutable: false,
                stimulus_id: null,
                stem_text: "Chứng minh công thức Bayes",
                options: [],
              },
            ],
            next_cursor: null,
            has_more: false,
            limit: 20,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.startsWith("/api/v1/questions?")) {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: q1Id,
                tenant_id: "22222222-2222-4222-8222-222222222222",
                visibility_scope: "TENANT_PRIVATE",
                stable_code: "C01_SAMPLE_MCQ_1",
                item_type: "SINGLE_CHOICE",
                primary_taxonomy_node_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
                curriculum_node_id: null,
                exam_framework_tags: ["THPTQG_2025"],
                difficulty_level: "TH",
                current_version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
                acquisition_status: "AUTHORED",
                verification_status: "UNVERIFIED",
                rights_status: "OWNED",
                publication_status: "DRAFT",
                access_tier: "FREE",
                created_at: "2026-11-15T00:00:00Z",
                version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
                version_no: 1,
                is_immutable: false,
                stimulus_id: null,
                stem_text: "Tính xác suất có điều kiện $P(A|B)$",
                options: [
                  { key: "A", text: "0.7" },
                  { key: "B", text: "0.3" },
                ],
              },
            ],
            next_cursor: "cur-page-2",
            has_more: true,
            limit: 20,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      return new Response("{}", { status: 404 });
    });

    render(<QuestionBankShell fetchFn={fetchMock as unknown as typeof fetch} />);

    await waitFor(() => {
      expect(screen.getByTestId("qb-card-C01_SAMPLE_MCQ_1")).toBeTruthy();
    });
    expect(screen.getByText(/Zero Answer Leak/i)).toBeTruthy();

    // Pagination appends page 2
    fireEvent.click(screen.getByTestId("qb-load-more-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("qb-card-C01_SAMPLE_ESSAY_2")).toBeTruthy();
    });

    // Click card -> inspect detail & fork draft version
    fireEvent.click(screen.getByTestId("qb-card-C01_SAMPLE_MCQ_1"));
    await waitFor(() => {
      expect(screen.getByTestId("qb-detail-panel")).toBeTruthy();
    });

    fireEvent.change(screen.getByTestId("qb-fork-reason-input"), {
      target: { value: "Cập nhật diễn đạt thử" },
    });
    fireEvent.change(screen.getByTestId("qb-fork-stem-input"), {
      target: { value: "Nội dung đã cập nhật v2" },
    });
    fireEvent.click(screen.getByTestId("qb-fork-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("question-bank-feedback").textContent).toContain("v2");
    });
    expect(forkCreatedCalls).toEqual([
      { change_reason: "Cập nhật diễn đạt thử", stem_text: "Nội dung đã cập nhật v2" },
    ]);
  });
});

describe("ImportsHubShell (/studio/imports)", () => {
  it("runs PDF -> documents -> extract -> exam-imports pipeline, supports FormData /imports queue, retry, and rejects .docx client-side", async () => {
    signInTeacher();
    const importId = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1";
    const docId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1";
    const extractId = "ffffffff-ffff-4fff-8fff-fffffffffff1";
    const createdBboxId = "99999999-9999-4999-8999-999999999991";
    const jobId = "77777777-7777-4777-8777-777777777771";

    const calledUrls: string[] = [];

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calledUrls.push(`${method} ${url}`);

      if (url.startsWith("/api/v1/exam-imports?") && method === "GET") {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: importId,
                extraction_run_id: extractId,
                document_version_id: null,
                title: "Đề Minh Họa Seeded",
                mime_type: "application/pdf",
                page_count: 1,
                status: "PENDING_REVIEW",
                rights_status: "OWNED",
                section_count: 1,
                question_count: 3,
                created_at: "2026-11-15T00:00:00Z",
              },
            ],
            next_cursor: null,
            has_more: false,
            limit: 20,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url.startsWith("/api/v1/imports?") && method === "GET") {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: jobId,
                source_id: null,
                document_version_id: null,
                job_type: "EXAM_IMPORT",
                job_status: "FAILED",
                current_stage: "EXTRACT",
                attempt_count: 1,
                last_error_code: "TIMEOUT",
                title: "De_Thi_Loi.pdf",
                mime_type: "application/pdf",
                page_count: 2,
                rights_status: "OWNED",
                created_at: "2026-11-15T00:00:00Z",
                updated_at: "2026-11-15T00:01:00Z",
              },
            ],
            next_cursor: null,
            has_more: false,
            limit: 20,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url === "/api/v1/documents" && method === "POST") {
        expect(init?.body instanceof FormData).toBe(true);
        const headers = init?.headers as Record<string, string> | undefined;
        expect(headers?.["Content-Type"]).toBeUndefined();
        return new Response(
          JSON.stringify({
            source_document_id: docId,
            document_version_id: "88888888-8888-4888-8888-888888888881",
            acquisition_job_id: jobId,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url === `/api/v1/documents/${docId}/extract` && method === "POST") {
        return new Response(JSON.stringify({ id: extractId }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (url === "/api/v1/exam-imports" && method === "POST") {
        return new Response(JSON.stringify({ id: createdBboxId, status: "PENDING_REVIEW" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (url === "/api/v1/imports" && method === "POST") {
        expect(init?.body instanceof FormData).toBe(true);
        return new Response(
          JSON.stringify({
            is_duplicate: false,
            idempotent_replay: false,
            job: { id: jobId, job_status: "QUEUED", job_type: "EXAM_IMPORT" },
            document_version: { id: "v1", title: "Queue Test", mime_type: "application/pdf" },
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }

      if (url === `/api/v1/imports/${jobId}/retry` && method === "POST") {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      return new Response("{}", { status: 404 });
    });

    render(<ImportsHubShell fetchFn={fetchMock as unknown as typeof fetch} />);

    await waitFor(() => {
      expect(screen.getByTestId(`review-link-${importId}`).getAttribute("href")).toBe(
        `/studio/imports/${importId}/review`,
      );
      expect(screen.getByTestId(`retry-job-${jobId}`)).toBeTruthy();
    });

    // Reject .zip before upload, allow .docx now
    const badFile = new File(["PK"], "goi-de.zip", {
      type: "application/zip",
    });
    fireEvent.change(screen.getByTestId("imports-file-input"), { target: { files: [badFile] } });
    fireEvent.click(screen.getByTestId("imports-upload-submit-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("workspace-api-error").textContent).toContain(".zip");
    });

    // Verify .docx proceeds through /documents -> /extract -> /exam-imports
    const docxFile = new File(["PK\x03\x04"], "de-cu.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(screen.getByTestId("imports-file-input"), { target: { files: [docxFile] } });
    fireEvent.change(screen.getByTestId("imports-title-input"), { target: { value: "Đề Word Test" } });
    fireEvent.click(screen.getByTestId("imports-upload-submit-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("open-created-bbox-link").getAttribute("href")).toBe(
        `/studio/imports/${createdBboxId}/review`,
      );
    });

    // Upload valid PDF through BBox pipeline
    const pdfFile = new File(["%PDF-1.4\n"], "de-toan.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByTestId("imports-file-input"), { target: { files: [pdfFile] } });
    fireEvent.change(screen.getByTestId("imports-title-input"), { target: { value: "Đề Toán Test" } });
    fireEvent.click(screen.getByTestId("imports-upload-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("open-created-bbox-link").getAttribute("href")).toBe(
        `/studio/imports/${createdBboxId}/review`,
      );
    });
    expect(calledUrls).toContain("POST /api/v1/documents");
    expect(calledUrls).toContain(`POST /api/v1/documents/${docId}/extract`);
    expect(calledUrls).toContain("POST /api/v1/exam-imports");

    // Test retry button on failed job
    fireEvent.click(screen.getByTestId(`retry-job-${jobId}`));
    await waitFor(() => {
      expect(calledUrls).toContain(`POST /api/v1/imports/${jobId}/retry`);
    });

    // Test direct A01 queue upload via FormData to /api/v1/imports
    const queueRadio = screen.getByRole("radio", { name: /Chỉ đẩy vào Hàng đợi Ingestion A01/i });
    fireEvent.click(queueRadio);
    fireEvent.click(screen.getByTestId("imports-upload-submit-btn"));
    await waitFor(() => {
      expect(calledUrls).toContain("POST /api/v1/imports");
    });
  });

  it("forwards multi-question extracted_structure sections and bbox_json from /extract to POST /api/v1/exam-imports", async () => {
    signInTeacher();
    const docId = "66666666-6666-4666-8666-666666666602";
    const extractId = "77777777-7777-4777-8777-777777777702";
    const createdBboxId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02";
    let postedExamImportBody: Record<string, unknown> | null = null;

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();

      if ((url.startsWith("/api/v1/exam-imports?") || url.startsWith("/api/v1/imports?")) && method === "GET") {
        return new Response(
          JSON.stringify({ items: [], next_cursor: null, has_more: false, limit: 20 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/v1/documents" && method === "POST") {
        return new Response(
          JSON.stringify({ source_document_id: docId, document_version_id: "88888888-8888-4888-8888-888888888882" }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === `/api/v1/documents/${docId}/extract` && method === "POST") {
        return new Response(
          JSON.stringify({
            id: extractId,
            extracted_structure: {
              pages: [{ page_no: 1, width_pt: 595, height_pt: 842, heading: "Trang 1" }],
              sections: [
                {
                  section_code: "PART_I_MCQ",
                  title: "PHẦN I",
                  questions: [
                    {
                      question_no: 1,
                      points: 0.25,
                      page_number: 1,
                      item_type: "SINGLE_CHOICE",
                      stem_md: "Câu 1: Giá trị của 1 + 1 bằng?",
                      options_md: ["1", "2", "3", "4"],
                      paired_answer: "B",
                      bbox_json: { x0: 42, y0: 100, x1: 520, y1: 185 },
                      original_question_label: "Câu 1",
                    },
                    {
                      question_no: 2,
                      points: 0.25,
                      page_number: 1,
                      item_type: "SINGLE_CHOICE",
                      stem_md: "Câu 2: Thủ đô Việt Nam là?",
                      options_md: ["Huế", "Đà Nẵng", "Hà Nội", "TP.HCM"],
                      paired_answer: "C",
                      bbox_json: { x0: 42, y0: 210, x1: 520, y1: 295 },
                      original_question_label: "Câu 2",
                    },
                  ],
                },
              ],
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/v1/exam-imports" && method === "POST") {
        postedExamImportBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({ id: createdBboxId, status: "PENDING_REVIEW" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("{}", { status: 404 });
    });

    render(<ImportsHubShell fetchFn={fetchMock as unknown as typeof fetch} />);
    const pdfFile = new File(["%PDF-1.4\n"], "de-minh-hoa.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByTestId("imports-file-input"), { target: { files: [pdfFile] } });
    fireEvent.click(screen.getByTestId("imports-upload-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("open-created-bbox-link").getAttribute("href")).toBe(
        `/studio/imports/${createdBboxId}/review`,
      );
    });
    expect(postedExamImportBody).not.toBeNull();
    const sent = postedExamImportBody as unknown as {
      expected_total_points: number;
      sections: Array<{ section_code: string; questions: Array<{ question_no: number; paired_answer: string; bbox_json: Record<string, number> }> }>;
    };
    expect(sent.expected_total_points).toBe(0.5);
    expect(sent.sections[0]?.questions).toHaveLength(2);
    expect(sent.sections[0]?.questions[0]?.paired_answer).toBe("B");
    expect(sent.sections[0]?.questions[1]?.bbox_json).toEqual({ x0: 42, y0: 210, x1: 520, y1: 295 });
  });
});

describe("Unified Studio, Home & Exam Builder Navigation Integration", () => {
  it("exposes /studio/questions and /studio/imports across HomePage, Headers, LiveWorkspacePanel, and hydrates MatrixExamBuilderShell", async () => {
    render(<HomePage />);
    const hrefs = screen.getAllByRole("link").map((el) => el.getAttribute("href"));
    expect(hrefs).toContain("/studio/questions");
    expect(hrefs).toContain("/studio/imports");
    expect(hrefs).toContain("/teacher/exam-builder");
    expect(hrefs).toContain("/admin/ops");
    cleanup();

    render(<StudioHeader activeRoute="ws4-question-bank" userTier="TEACHER_PRO" />);
    expect(screen.getByRole("link", { name: "Ngân hàng câu hỏi" }).getAttribute("href")).toBe(
      "/studio/questions",
    );
    expect(screen.getByRole("link", { name: "Nạp file đề" }).getAttribute("href")).toBe(
      "/studio/imports",
    );
    cleanup();

    // Teacher-only session hides Studio/Bank/Admin links in TeacherHeader
    signInTeacher(["TEACHER"]);
    render(<TeacherHeader activeRoute="exam-builder" userTier="TEACHER_PRO" />);
    expect(screen.queryByRole("link", { name: "Ngân hàng câu hỏi" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Nạp file đề" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Admin Ops" })).toBeNull();
    cleanup();

    // Content author / Admin session exposes Studio links
    signInTeacher(["CONTENT_AUTHOR"]);
    render(<TeacherHeader activeRoute="exam-builder" userTier="TEACHER_PRO" />);
    expect(screen.getByRole("link", { name: "Ngân hàng câu hỏi" }).getAttribute("href")).toBe(
      "/studio/questions",
    );
    expect(screen.getByRole("link", { name: "Nạp file đề" }).getAttribute("href")).toBe(
      "/studio/imports",
    );
    cleanup();

    signInTeacher();
    const emptyArraysFetch = vi.fn(
      async () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    render(<LiveWorkspacePanel fetchFn={emptyArraysFetch as unknown as typeof fetch} />);
    await waitFor(() => {
      expect(screen.getByTestId("live-workspace-staff-tools")).toBeTruthy();
      expect(screen.getByTestId("live-workspace-empty")).toBeTruthy();
    });
    cleanup();

    const realPaperId = "12121212-1212-4121-8121-121212121212";
    const assessmentsFetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/assessments") {
        return new Response(
          JSON.stringify([
            { id: realPaperId, title: "Đề Toán Thử Real UUID", publication_status: "PUBLISHED" },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    });

    render(<MatrixExamBuilderShell fetchFn={assessmentsFetch as unknown as typeof fetch} />);
    await waitFor(() => {
      const selector = screen.getByTestId("live-assessment-select") as HTMLSelectElement;
      expect(selector.value).toBe(realPaperId);
      expect(selector.selectedOptions[0]?.textContent).toContain("(PUBLISHED)");
    });
  });
});
