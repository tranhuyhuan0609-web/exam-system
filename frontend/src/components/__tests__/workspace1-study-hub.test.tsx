// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import StudyDocumentPage from "@/app/hoc-lieu/[slug]/page";
import LearningPathPage from "@/app/lo-trinh/page";
import ErrorNotebookPage from "@/app/so-tay-cau-sai/page";
import { ErrorNotebookShell } from "@/components/study-hub/ErrorNotebookShell";
import { LearningPathShell } from "@/components/study-hub/LearningPathShell";
import { StudyHubShell } from "@/components/study-hub/StudyHubShell";
import { createDemoStudyDocument } from "@/components/study-hub/types";

afterEach(() => {
  cleanup();
});

describe("Workspace 1: Learner Study & Practice Hub (TASK-25)", () => {
  it("renders H2Edu branding and the 3-column collapsible layout (260px / 760px / 320px)", () => {
    render(<StudyHubShell slug="cong-thuc-bayes" />);

    expect(screen.getByTestId("h2edu-study-header").textContent).toContain("H2Edu");
    expect(screen.getByTestId("h2edu-study-header").textContent).toContain("Học thông minh - Thi bứt phá");

    const sidebar = screen.getByTestId("workspace1-curriculum-sidebar");
    const readingCol = screen.getByTestId("workspace1-reading-column");
    const drawer = screen.getByTestId("workspace1-tutor-drawer");

    expect(sidebar.className).toContain("lg:w-[260px]");
    expect(readingCol.className).toContain("max-w-[760px]");
    expect(drawer.className).toContain("lg:w-[320px]");

    // Collapsible left sidebar
    const collapseBtn = screen.getByTestId("toggle-curriculum-sidebar");
    fireEvent.click(collapseBtn);
    expect(screen.getByTestId("workspace1-curriculum-sidebar").className).toContain("lg:w-[56px]");
    fireEvent.click(collapseBtn);
    expect(screen.getByTestId("workspace1-curriculum-sidebar").className).toContain("lg:w-[260px]");
  });

  it("renders the 4-level SGK curriculum tree with D06 Mastery color dots", () => {
    const { container } = render(<StudyHubShell slug="cong-thuc-bayes" />);

    expect(container.querySelector('[data-level="CHAPTER"]')).toBeTruthy();
    expect(container.querySelector('[data-level="LESSON"]')).toBeTruthy();
    expect(container.querySelector('[data-level="SECTION"]')).toBeTruthy();
    expect(container.querySelectorAll('[data-level="PROBLEM_TYPE"]').length).toBe(2);

    expect(screen.getByTestId("mastery-dot-les-19").getAttribute("data-mastery-color")).toBe("GREEN");
    expect(screen.getByTestId("mastery-dot-sec-19-1").getAttribute("data-mastery-color")).toBe("YELLOW");
    expect(screen.getByTestId("mastery-dot-pt-bayes-y-sinh").getAttribute("data-mastery-color")).toBe("RED");
    // < 20 answered & SE >= 0.35 -> GRAY (INSUFFICIENT_DATA)
    expect(screen.getByTestId("mastery-dot-pt-bayes-may-moc").getAttribute("data-mastery-color")).toBe("GRAY");
    expect(screen.getByTestId("mastery-dot-pt-bayes-may-moc").getAttribute("data-mastery-status")).toBe(
      "INSUFFICIENT_DATA",
    );
  });

  it("renders all 5 VietJack blocks with Xem/An loi giai toggle and safe KaTeX XSS filtering", () => {
    const base = createDemoStudyDocument("cong-thuc-bayes");
    const docWithXss = {
      ...base,
      blocks: [
        ...base.blocks,
        {
          id: "blk-xss",
          kind: "TIP_WARNING" as const,
          title: "Kiểm tra bảo mật LaTeX",
          content: "Công thức an toàn $P(A) = 0.5$ và payload độc $\\href{javascript:alert(1)}{click}$.",
        },
      ],
    };

    const { container } = render(<StudyHubShell slug="cong-thuc-bayes" initialDocument={docWithXss} />);

    expect(screen.getByTestId("study-block-THEORY_SUMMARY")).toBeTruthy();
    expect(screen.getByTestId("study-block-FORMULA_BOX")).toBeTruthy();
    expect(screen.getByTestId("study-block-METHOD_STEPS")).toBeTruthy();
    expect(screen.getAllByTestId("study-block-TIP_WARNING").length).toBe(2);
    expect(screen.getByTestId("study-block-WORKED_EXAMPLE")).toBeTruthy();

    // KaTeX rendered safely and XSS command blocked
    expect(container.querySelectorAll('[data-katex-rendered="true"]').length).toBeGreaterThan(0);
    expect(container.querySelector('[data-katex-blocked="true"]')).toBeTruthy();
    expect(container.innerHTML).not.toContain("javascript:alert(1)");

    // Worked example toggle Xem/Ẩn lời giải
    expect(screen.queryByTestId("worked-solution-blk-example")).toBeNull();
    fireEvent.click(screen.getByTestId("toggle-solution-blk-example"));
    expect(screen.getByTestId("worked-solution-blk-example")).toBeTruthy();
    fireEvent.click(screen.getByTestId("toggle-solution-blk-example"));
    expect(screen.queryByTestId("worked-solution-blk-example")).toBeNull();
  });

  it("supports the 4 interactive practice tabs, numeric 1.000 warning, and error-note capture", () => {
    render(<StudyHubShell slug="cong-thuc-bayes" />);

    expect(screen.getByTestId("practice-tab-ESSAY")).toBeTruthy();
    expect(screen.getByTestId("practice-tab-SINGLE_CHOICE")).toBeTruthy();
    expect(screen.getByTestId("practice-tab-CLUSTER_TRUE_FALSE")).toBeTruthy();
    expect(screen.getByTestId("practice-tab-SHORT_NUMERIC")).toBeTruthy();

    // Wrong ABCD answer logs into error notebook drawer
    fireEvent.click(screen.getByTestId("practice-option-prac-abcd-1-B"));
    fireEvent.click(screen.getByTestId("check-practice-prac-abcd-1"));
    expect(screen.getByTestId("practice-feedback-prac-abcd-1").textContent).toContain("Chưa chính xác");
    expect(screen.getByTestId("drawer-error-note-prac-err-prac-abcd-1")).toBeTruthy();

    // Step hint toggle
    fireEvent.click(screen.getByTestId("hint-practice-prac-abcd-1"));
    expect(screen.getByTestId("practice-hint-prac-abcd-1")).toBeTruthy();

    // Short numeric tab rejects 1.000 thousands separator
    fireEvent.click(screen.getByTestId("practice-tab-SHORT_NUMERIC"));
    const input = screen.getByTestId("short-input-prac-short-1") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "1.000" } });
    expect(screen.getByTestId("short-error-prac-short-1").textContent).toContain("1.000");

    // Valid Vietnamese decimal comma works
    fireEvent.change(input, { target: { value: "0,5" } });
    fireEvent.click(screen.getByTestId("check-practice-prac-short-1"));
    expect(screen.getByTestId("practice-feedback-prac-short-1").textContent).toContain("Chính xác");
  });

  it("enforces fail-closed Locked Pro Preview for FREE users (no raw URL in DOM) and unlocks for STUDENT_PRO", () => {
    const proDoc = createDemoStudyDocument("bo-de-thptqg-2025-pro");
    expect(proDoc.requiresProAsset).toBe(true);
    expect(proDoc.rawAssetUrl).toBeTruthy();

    const { container } = render(
      <StudyHubShell slug="bo-de-thptqg-2025-pro" initialDocument={proDoc} initialUserTier="FREE" />,
    );

    expect(screen.getByTestId("workspace-locked-pro-preview")).toBeTruthy();
    expect(screen.getByTestId("pro-preview-pdf-box").textContent).toContain(proDoc.previewPdfUrl!);
    // CRITICAL SECURITY CHECK: original Pro PDF URL must NEVER leak to DOM for FREE user
    expect(container.innerHTML).not.toContain(proDoc.rawAssetUrl!);

    // Switching to PRO unlocks full 3-column reading view
    fireEvent.click(screen.getByTestId("toggle-tier-btn"));
    expect(screen.getByTestId("workspace1-three-column-layout")).toBeTruthy();
  });

  it("supports 3 AI Tutor modes and locks tutor when an active exam attempt is running", () => {
    const view = render(<StudyHubShell slug="cong-thuc-bayes" />);

    fireEvent.click(screen.getByTestId("tutor-mode-EXPLAIN_MODE"));
    expect(screen.getByTestId("tutor-active-mode-badge").textContent).toBe("EXPLAIN_MODE");

    fireEvent.change(screen.getByTestId("tutor-question-input"), {
      target: { value: "Giải thích mẫu số P(B)" },
    });
    fireEvent.click(screen.getByTestId("tutor-send-btn"));
    expect(screen.getAllByTestId("tutor-message-ai").at(-1)?.textContent).toContain("EXPLAIN_MODE");

    view.unmount();

    // Active exam lock -> 403 EXAM_SESSION_ACTIVE guard
    render(<StudyHubShell slug="cong-thuc-bayes" activeExamAttemptId="att-live-99" />);
    expect(screen.getByTestId("tutor-exam-lock-alert").textContent).toContain("403 EXAM_SESSION_ACTIVE");
    expect((screen.getByTestId("tutor-question-input") as HTMLInputElement).disabled).toBe(true);
  });

  it("renders /lo-trinh rationale_text + teacher lock and resolves /so-tay-cau-sai only at >= 2 consecutive correct", async () => {
    const pathView = render(<LearningPathShell />);
    expect(screen.getByTestId("learning-path-main")).toBeTruthy();
    expect(screen.getByTestId("task-rationale-lp-prereq-1").textContent).toContain("tiên quyết");
    expect(screen.getByTestId("teacher-locked-lp-teacher-3")).toBeTruthy();
    fireEvent.click(screen.getByTestId("recalculate-plan-btn"));
    expect(screen.getByTestId("recalculate-notice").textContent).toContain("bảo toàn 100%");
    expect(screen.getByTestId("teacher-locked-lp-teacher-3")).toBeTruthy();
    pathView.unmount();

    render(<ErrorNotebookShell />);
    expect(screen.getByTestId("error-note-status-en-bayes-02").textContent).toContain("ACTIVE · 0/2");

    // 1st correct attempt on en-bayes-02 -> stays ACTIVE (1/2)
    fireEvent.click(screen.getByTestId("drill-opt-en-bayes-02-C"));
    fireEvent.click(screen.getByTestId("submit-drill-en-bayes-02"));
    expect(screen.getByTestId("error-note-status-en-bayes-02").textContent).toContain("ACTIVE · 1/2");

    // 2nd consecutive correct attempt on en-bayes-02 -> becomes RESOLVED (2/2)
    fireEvent.click(screen.getByTestId("submit-drill-en-bayes-02"));
    expect(screen.getByTestId("error-note-status-en-bayes-02").textContent).toContain("RESOLVED · 2/2");

    cleanup();
    // Route pages resolve cleanly
    const studyEl = await StudyDocumentPage({
      params: Promise.resolve({ slug: "cong-thuc-bayes" }),
      searchParams: Promise.resolve({}),
    });
    render(studyEl);
    expect(screen.getByTestId("workspace1-three-column-layout")).toBeTruthy();
    cleanup();

    render(<LearningPathPage />);
    expect(screen.getByTestId("learning-path-main")).toBeTruthy();
    cleanup();

    render(<ErrorNotebookPage />);
    expect(screen.getByTestId("error-notebook-main")).toBeTruthy();
  });

  it("renders mandatory LoadingSkeleton, EmptyState, ErrorBoundary, OfflineRetryBanner, and SequenceConflictModal", () => {
    const view1 = render(
      <StudyHubShell slug="cong-thuc-bayes" initialUiOverrides={{ isLoading: true }} />,
    );
    expect(screen.getByTestId("workspace-loading-skeleton")).toBeTruthy();
    view1.unmount();

    const base = createDemoStudyDocument("cong-thuc-bayes");
    const view2 = render(
      <StudyHubShell
        slug="cong-thuc-bayes"
        initialDocument={{ ...base, blocks: [], practiceItems: [] }}
      />,
    );
    expect(screen.getByTestId("workspace-empty-state")).toBeTruthy();
    view2.unmount();

    const view3 = render(
      <StudyHubShell slug="cong-thuc-bayes" initialUiOverrides={{ fatalErrorCode: "E_NET" }} />,
    );
    expect(screen.getByTestId("workspace-error-boundary").textContent).toContain("E_NET");
    view3.unmount();

    const view4 = render(
      <StudyHubShell
        slug="cong-thuc-bayes"
        initialUiOverrides={{ isOffline: true, pendingAutosaveCount: 3 }}
      />,
    );
    expect(screen.getByTestId("workspace-offline-retry-banner")).toBeTruthy();
    view4.unmount();

    render(
      <StudyHubShell slug="cong-thuc-bayes" initialUiOverrides={{ isSequenceConflict409: true }} />,
    );
    expect(screen.getByTestId("workspace-sequence-conflict-modal")).toBeTruthy();
  });
});
