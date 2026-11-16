// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import AdminOpsPage from "@/app/admin/ops/page";
import AdminSourcesPage from "@/app/admin/sources/page";
import AdminTakedownsPage from "@/app/admin/takedowns/page";
import TeacherClassPage from "@/app/teacher/classes/[id]/page";
import TeacherExamBuilderPage from "@/app/teacher/exam-builder/page";
import { AdminOpsConsoleShell } from "@/components/admin-ops/AdminOpsConsoleShell";
import { AdminSourcesShell } from "@/components/admin-ops/AdminSourcesShell";
import { AdminTakedownsShell } from "@/components/admin-ops/AdminTakedownsShell";
import { MatrixExamBuilderShell } from "@/components/teacher-lms/MatrixExamBuilderShell";
import { TeacherClassLmsShell } from "@/components/teacher-lms/TeacherClassLmsShell";
import {
  createDemoMatrixBuilderData,
  createDemoTeacherClassData,
} from "@/components/teacher-lms/types";

afterEach(() => {
  cleanup();
});

describe("Workspace 5: Matrix Exam Builder THPTQG 2025+ (TASK-27)", () => {
  it("highlights matrix shortages in red, blocks Assemble until resolved, and switches to NATURAL_SCIENCE 28-item preset", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<MatrixExamBuilderShell fetchFn={fetchMock as unknown as typeof fetch} />);

    // Default Bayes VDC SHORT_NUMERIC has desired=2 > available=1 -> shortage!
    const shortCell = screen.getByTestId("matrix-cell-TOAN12_XAC_SUAT_BAYES-VDC-SHORT_NUMERIC");
    expect(shortCell.getAttribute("data-shortage")).toBe("true");
    expect(screen.getByTestId("shortage-report-panel")).toBeTruthy();

    const assembleBtn = screen.getByTestId("assemble-exam-btn") as HTMLButtonElement;
    expect(assembleBtn.disabled).toBe(true);

    // Switch to NATURAL_SCIENCE preset -> 18 / 4 / 6 = 28 questions
    fireEvent.click(screen.getByTestId("preset-science-btn"));
    expect(screen.getByTestId("part1-count").textContent?.trim()).toBe("18 câu");
    expect(screen.getByTestId("part2-count").textContent?.trim()).toBe("4 câu");
    expect(screen.getByTestId("part3-count").textContent?.trim()).toBe("6 câu");

    // Trigger AI Gap Filler E03 -> A09 on science shortage
    fireEvent.click(screen.getByTestId("trigger-ai-gap-fill-btn"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/ai/generations",
        expect.objectContaining({ method: "POST" }),
      );
    });

    // Switch back to MATH and balance desired cells within bank inventory (22 items valid)
    fireEvent.click(screen.getByTestId("preset-math-btn"));
    fireEvent.change(screen.getByTestId("matrix-input-TOAN12_XAC_SUAT_BAYES-VDC-SHORT_NUMERIC"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByTestId("matrix-input-TOAN12_MU_LOG-VDC-SHORT_NUMERIC"), {
      target: { value: "1" },
    });

    expect(screen.getByTestId("shortage-clear-banner")).toBeTruthy();
    expect((screen.getByTestId("assemble-exam-btn") as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByTestId("assemble-exam-btn"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/assessments/asm-thptqg-math-2025/assemble",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});

describe("Workspace 5: Teacher Class LMS & Copilot Confirmation (TASK-27)", () => {
  it("renders score distribution, unsubmitted students, Top 5 Item Analysis, CSV export, and Copilot confirmation gate", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 201 }));
    render(<TeacherClassLmsShell classId="class-12a1" fetchFn={fetchMock as unknown as typeof fetch} />);

    expect(screen.getByTestId("score-distribution-panel")).toBeTruthy();
    expect(screen.getByTestId("unsubmitted-students-panel").textContent).toContain("HS12006");
    expect(screen.getByTestId("item-analysis-panel")).toBeTruthy();
    expect(screen.getByTestId("item-analysis-card-LEG-19").textContent).toContain("45.8%");

    // Gradebook CSV export
    fireEvent.click(screen.getByTestId("export-gradebook-csv-btn"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/classes/class-12a1/gradebook/export");
    });
    expect(screen.getByTestId("exported-csv-preview").textContent).toContain("student_code");

    // E06 Copilot MUST require explicit teacher confirmation before POST /api/v1/assignments
    fireEvent.click(screen.getByTestId("assign-remediation-btn-cluster-bayes-01"));
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/v1/assignments",
      expect.anything(),
    );

    fireEvent.click(screen.getByTestId("confirm-remediation-btn-cluster-bayes-01"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/assignments",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(screen.getByTestId("remediation-assigned-badge-cluster-bayes-01")).toBeTruthy();
  });
});

describe("Workspace 6: Admin Ops, Sources Pairing/Drift, & Takedowns Audit (TASK-27)", () => {
  it("alerts Outbox SLO_BREACH (>90s) and enforces >= 15-char reason modal for DLQ replay and grade override", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<AdminOpsConsoleShell fetchFn={fetchMock as unknown as typeof fetch} />);

    expect(screen.getByTestId("outbox-slo-panel").textContent).toContain("SLO_BREACH");

    // Open Replay DLQ modal -> short reason blocked
    fireEvent.click(screen.getByTestId("replay-dlq-btn-dlq-101"));
    expect(screen.getByTestId("admin-reason-modal")).toBeTruthy();
    const confirmBtn = screen.getByTestId("admin-reason-confirm-btn") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("admin-reason-input"), { target: { value: "quá ngắn" } });
    expect(confirmBtn.disabled).toBe(true);
    expect(screen.getByTestId("admin-reason-validation-msg")).toBeTruthy();

    // Valid >= 15-char audit reason
    fireEvent.change(screen.getByTestId("admin-reason-input"), {
      target: { value: "Đã vá parser bảng 3 cột A06 và replay lại event bị kẹt DLQ" },
    });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/admin/dlq/dlq-101/replay",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(screen.getByTestId("ops-audit-log-panel").textContent).toContain("REPLAY_DLQ");

    // Feature flag toggle + Grade override modal
    fireEvent.click(screen.getByTestId("toggle-flag-btn-parser.vlm_table_fallback"));
    fireEvent.click(screen.getByTestId("override-grade-audit-btn"));
    fireEvent.change(screen.getByTestId("admin-reason-input"), {
      target: { value: "Phúc khảo biên bản phòng thi 12A1 xác nhận đáp án đúng" },
    });
    // The backend (OverrideRequest) needs total_score as well: no score, no confirm; 3 decimals / > 1000 refused.
    const overrideConfirm = screen.getByTestId("admin-reason-confirm-btn") as HTMLButtonElement;
    expect(overrideConfirm.disabled).toBe(true);
    for (const bad of ["", "abc", "7.255", "1000.01", "-1"]) {
      fireEvent.change(screen.getByTestId("admin-override-score-input"), { target: { value: bad } });
      expect(overrideConfirm.disabled).toBe(true);
    }
    fireEvent.change(screen.getByTestId("admin-override-score-input"), { target: { value: "7,25" } });
    expect(overrideConfirm.disabled).toBe(false);
    fireEvent.click(overrideConfirm);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/^\/api\/v1\/grades\/.+\/override$/),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            total_score: "7.25",
            reason: "Phúc khảo biên bản phòng thi 12A1 xác nhận đáp án đúng",
          }),
        }),
      );
    });
  });

  it("manages Google Picker, 4-signal Drive pairing gate, Parser Drift PAUSED_DRIFT, and mandatory audit on rights change", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<AdminSourcesShell fetchFn={fetchMock as unknown as typeof fetch} />);

    // Google Picker drawer
    expect(screen.getByTestId("google-drive-picker-panel").textContent).toContain("drive.file");
    fireEvent.click(screen.getByTestId("open-google-picker-btn"));
    expect(screen.getByTestId("google-picker-drawer")).toBeTruthy();
    fireEvent.click(screen.getByTestId("confirm-picker-selection-btn"));

    // Pairing candidate check: pair-01 (22==22) enabled, pair-02 (28!=25) disabled
    expect((screen.getByTestId("confirm-pairing-btn-pair-01") as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect((screen.getByTestId("confirm-pairing-btn-pair-02") as HTMLButtonElement).disabled).toBe(
      true,
    );

    fireEvent.click(screen.getByTestId("confirm-pairing-btn-pair-01"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/drive/pairings/pair-01/confirm",
        expect.objectContaining({ method: "POST" }),
      );
    });

    // Parser drift alert on src-crawl-02 (7/30 > 15% over 30 consecutive pages)
    expect(screen.getByTestId("drift-status-src-crawl-02").textContent).toContain("PAUSED_DRIFT");

    // Rights change enforces AdminReasonModal >= 15 chars
    fireEvent.click(screen.getByTestId("change-rights-btn-src-crawl-01"));
    fireEvent.change(screen.getByTestId("admin-reason-input"), {
      target: { value: "Cập nhật hợp đồng cấp phép nguồn VTED gia hạn năm 2026" },
    });
    fireEvent.click(screen.getByTestId("admin-reason-confirm-btn"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/rights-records",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("enforces mandatory audit reason before Legal Takedown execution and transitions rights to TAKEDOWN", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<AdminTakedownsShell fetchFn={fetchMock as unknown as typeof fetch} />);

    fireEvent.click(screen.getByTestId("execute-takedown-btn-td-2026-01"));
    const confirmBtn = screen.getByTestId("admin-reason-confirm-btn") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    // Reject < 15 chars
    fireEvent.change(screen.getByTestId("admin-reason-input"), { target: { value: "DMCA takedown" } });
    expect(confirmBtn.disabled).toBe(true);

    // Accept >= 15 chars -> execute legal takedown
    fireEvent.change(screen.getByTestId("admin-reason-input"), {
      target: { value: "Xác nhận công văn DMCA-VN-2026-1114-01 hợp lệ từ pháp chế NXB A" },
    });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/takedown-cases/td-2026-01/execute",
        expect.objectContaining({ method: "POST" }),
      );
    });

    expect(screen.getByTestId("takedown-rights-status-td-2026-01").textContent?.trim()).toBe(
      "TAKEDOWN",
    );
    expect(screen.getByTestId("takedown-executed-badge-td-2026-01")).toBeTruthy();
    expect(screen.getByTestId("takedowns-audit-feed").textContent).toContain("EXECUTE_TAKEDOWN");
  });
});

describe("Workspace 5 & 6 Routes, Home Navigation, & Mandatory 6 UI States", () => {
  it("resolves all 5 WS5/WS6 Next.js routes and exposes links for all 6 Workspaces on Home", async () => {
    const homeView = render(<HomePage />);
    const hrefs = Array.from(homeView.container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/teacher/exam-builder");
    expect(hrefs).toContain("/teacher/classes/class-12a1");
    expect(hrefs).toContain("/admin/ops");
    expect(hrefs).toContain("/admin/sources");
    expect(hrefs).toContain("/admin/takedowns");
    homeView.unmount();

    render(<TeacherExamBuilderPage />);
    expect(screen.getByTestId("h2edu-teacher-header")).toBeTruthy();
    cleanup();

    // sample data is only for `?demo=1`
    window.history.pushState({}, "", "/?demo=1");
    const classEl = await TeacherClassPage({
      params: Promise.resolve({ id: "class-12a1" }),
      searchParams: Promise.resolve({}),
    });
    render(classEl);
    expect(screen.getByTestId("gradebook-table-panel")).toBeTruthy();
    cleanup();
    window.history.pushState({}, "", "/");

    render(<AdminOpsPage />);
    expect(screen.getByTestId("outbox-slo-panel")).toBeTruthy();
    cleanup();

    render(<AdminSourcesPage />);
    expect(screen.getByTestId("google-drive-picker-panel")).toBeTruthy();
    cleanup();

    render(<AdminTakedownsPage />);
    expect(screen.getByTestId("takedowns-audit-feed")).toBeTruthy();
  });

  it("enforces all 6 mandatory UI states including fail-closed LockedProPreview without rawAssetUrl leak", () => {
    const proClassData = {
      ...createDemoTeacherClassData("class-12a1"),
      requiresProAsset: true,
      rawAssetUrl: "https://cdn.internal/secret-pro-teacher-report.pdf",
      previewPdfUrl: "https://cdn.public/preview-2pages.pdf",
    };

    const proView = render(
      <TeacherClassLmsShell
        classId="class-12a1"
        initialData={proClassData}
        initialUserTier="FREE"
      />,
    );
    expect(screen.getByTestId("workspace-locked-pro-preview")).toBeTruthy();
    expect(proView.container.innerHTML).not.toContain("secret-pro-teacher-report.pdf");
    proView.unmount();

    const proMatrixData = {
      ...createDemoMatrixBuilderData("MATH"),
      requiresProAsset: true,
      rawAssetUrl: "https://cdn.internal/secret-raw-exam-bank.pdf",
    };
    const states: Array<{ overrides: Record<string, unknown>; testId: string }> = [
      { overrides: { isLoading: true }, testId: "workspace-loading-skeleton" },
      { overrides: { itemCount: 0 }, testId: "workspace-empty-state" },
      { overrides: { fatalErrorCode: "E_WS5_500" }, testId: "workspace-error-boundary" },
      {
        overrides: { isOffline: true, pendingAutosaveCount: 3 },
        testId: "workspace-offline-retry-banner",
      },
      { overrides: { isSequenceConflict409: true }, testId: "workspace-sequence-conflict-modal" },
    ];

    for (const s of states) {
      const view = render(
        <MatrixExamBuilderShell initialData={proMatrixData} initialUiOverrides={s.overrides} />,
      );
      expect(screen.getByTestId(s.testId)).toBeTruthy();
      view.unmount();
    }
  });
});

describe("Teacher PDF-to-Exam Room & Admin Live Ingest (Toán 12)", () => {
  it("submits Teacher PDF upload via POST /api/v1/teacher/assessments/from-pdf in MatrixExamBuilderShell Tab 2", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/teacher/assessments/from-pdf" && init?.method === "POST") {
        expect(init.body instanceof FormData).toBe(true);
        return new Response(
          JSON.stringify({
            assessment_id: "11111111-1111-4111-8111-111111111111",
            assessment_version_id: "22222222-2222-4222-8222-222222222222",
            assignment_id: "33333333-3333-4333-8333-333333333333",
            title: "Đề Toán 12 Chương Bayes",
            question_count: 10,
            skipped_question_count: 1,
            duration_minutes: 45,
            exam_room_url: "/teacher/classes/class-12a1",
            assignment_url: "/teacher/classes/class-12a1?assignment_id=33333333-3333-4333-8333-333333333333",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    });

    render(<MatrixExamBuilderShell fetchFn={fetchMock as unknown as typeof fetch} />);
    fireEvent.click(screen.getByTestId("tab-from-pdf"));

    const pdf = new File(["%PDF-1.4\n"], "de-toan-12.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByTestId("teacher-pdf-file-input"), { target: { files: [pdf] } });
    fireEvent.click(screen.getByTestId("teacher-pdf-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-pdf-result-panel")).toBeTruthy();
      expect(screen.getByTestId("teacher-pdf-result-panel").textContent).toContain("10 câu");
      expect(screen.getByTestId("teacher-pdf-open-class-link").getAttribute("href")).toContain(
        "/teacher/classes/class-12a1",
      );
    });
  });

  it("submits live Crawler and Drive fetch-and-import actions in AdminSourcesShell", async () => {
    const srcId = "44444444-4444-4444-8444-444444444444";
    const connId = "55555555-5555-4555-8555-555555555555";
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `/api/v1/crawler/sources/${srcId}/fetch-and-import`) {
        return new Response(
          JSON.stringify({
            source_id: srcId,
            url: "https://de.example.edu.vn/toan-12.html",
            http_status: 200,
            fetched_bytes: 1200,
            extracted_count: 5,
            created_count: 4,
            duplicate_count: 1,
            created_question_ids: [],
            duplicate_question_ids: [],
            skipped: [],
            drift_detected: false,
            source_policy_status: "ACTIVE",
            fetch_id: "66666666-6666-4666-8666-666666666666",
            warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === `/api/v1/drive/connections/${connId}/fetch-and-import`) {
        return new Response(
          JSON.stringify({
            connection_id: connId,
            folder_id: "1AbCdEfGhIjKlMnOpQrStUv",
            listed: 2,
            created_question_count: 8,
            content_item_count: 1,
            files: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("{}", { status: 404 });
    });

    render(<AdminSourcesShell fetchFn={fetchMock as unknown as typeof fetch} />);
    fireEvent.change(screen.getByTestId("crawler-source-id-input"), { target: { value: srcId } });
    fireEvent.change(screen.getByTestId("crawler-fetch-url-input"), {
      target: { value: "https://de.example.edu.vn/toan-12.html" },
    });
    fireEvent.click(screen.getByTestId("crawler-fetch-import-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("crawler-fetch-result").textContent).toContain("Tạo mới: 4");
    });

    fireEvent.change(screen.getByTestId("drive-conn-id-input"), { target: { value: connId } });
    fireEvent.change(screen.getByTestId("drive-folder-id-input"), {
      target: { value: "1AbCdEfGhIjKlMnOpQrStUv" },
    });
    fireEvent.click(screen.getByTestId("drive-fetch-import-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("drive-fetch-result").textContent).toContain("Câu hỏi tạo mới: 8");
    });
  });
});
