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

    const classEl = await TeacherClassPage({
      params: Promise.resolve({ id: "class-12a1" }),
      searchParams: Promise.resolve({}),
    });
    render(classEl);
    expect(screen.getByTestId("gradebook-table-panel")).toBeTruthy();
    cleanup();

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
