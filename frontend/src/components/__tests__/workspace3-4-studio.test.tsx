// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import StudioContentPage from "@/app/studio/content/[id]/page";
import ImportReviewPage from "@/app/studio/imports/[run_id]/review/page";
import NewQuestionStudioPage from "@/app/studio/questions/new/page";
import { QuestionAuthoringShell } from "@/components/studio-authoring/QuestionAuthoringShell";
import { StudioContentEditorShell } from "@/components/studio-authoring/StudioContentEditorShell";
import { BboxReviewerShell } from "@/components/studio-review/BboxReviewerShell";
import { createDemoImportReviewSession } from "@/components/studio-review/types";

afterEach(() => {
  cleanup();
});

describe("Workspace 3: Side-by-Side Bounding-Box Reviewer (TASK-26)", () => {
  it("renders 50/50 split layout with color-coded SVG bbox overlays and switches page/zoom on item select", () => {
    const { container } = render(<BboxReviewerShell runId="run-bayes-2025" />);

    const split = screen.getByTestId("ws3-split-layout");
    expect(split.textContent).toContain("Side-by-Side 50% / 50% BBox Reviewer");
    expect(screen.getByTestId("ws3-left-canvas-pane")).toBeTruthy();
    expect(screen.getByTestId("ws3-right-inspector-pane")).toBeTruthy();

    // Verify Page 1 has QUESTION (blue), FIGURE_CROP (orange), HTML_TABLE (purple) overlays
    expect(container.querySelector('[data-bbox-kind="QUESTION"]')).toBeTruthy();
    expect(container.querySelector('[data-bbox-kind="FIGURE_CROP"]')).toBeTruthy();
    expect(container.querySelector('[data-bbox-kind="HTML_TABLE"]')).toBeTruthy();

    // Selecting Question 3 (Page 2) automatically focuses Page 2 & zooms to 120%
    fireEvent.click(screen.getByTestId("ws3-select-item-cand-q3"));
    const canvasPane = screen.getByTestId("ws3-left-canvas-pane");
    expect(canvasPane.getAttribute("data-focused-item-id")).toBe("cand-q3");
    expect(canvasPane.getAttribute("data-focused-page")).toBe("2");
    expect(screen.getByTestId("ws3-zoom-label").textContent).toBe("120%");
  });

  it("supports drag re-crop on SVG overlay clamped to page bounds", () => {
    render(<BboxReviewerShell runId="run-crop-01" />);

    fireEvent.click(screen.getByTestId("ws3-kind-btn-FIGURE_CROP"));
    const svg = screen.getByTestId("ws3-bbox-svg");

    // Drag with swapped/out-of-bounds coordinates -> normalizeAndClampBBox normalizes & clamps [0..595] x [0..842]
    fireEvent.mouseDown(svg, { clientX: 640, clientY: 900 });
    fireEvent.mouseMove(svg, { clientX: -12, clientY: 180 });
    fireEvent.mouseUp(svg, { clientX: -12, clientY: 180 });

    const coords = screen.getByTestId("ws3-active-bbox-coords").textContent ?? "";
    expect(coords).toContain("FIGURE_CROP");
    expect(coords).toContain("x0=0, y0=180, x1=595, y1=842");
  });

  it("flags DISPUTED_ANSWER from SymPy mismatch and hard-locks Publish/Approve until rights + VERIFIED clear", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<BboxReviewerShell runId="run-gate-01" fetchFn={fetchMock as unknown as typeof fetch} />);

    // Question 1 starts with DISPUTED_ANSWER (0.42 vs 0.33), PENDING_REVIEW rights, DISPUTED verification
    expect(screen.getByTestId("ws3-disputed-answer-badge").textContent).toBe("DISPUTED_ANSWER");
    expect((screen.getByTestId("ws3-approve-btn") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("ws3-publish-btn") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("ws3-mark-verified-btn") as HTMLButtonElement).disabled).toBe(true);

    // Fix source answer to match SymPy (0.33) -> DISPUTED_ANSWER disappears
    fireEvent.change(screen.getByTestId("ws3-source-answer-input"), { target: { value: "0,33" } });
    expect(screen.queryByTestId("ws3-disputed-answer-badge")).toBeNull();
    expect(screen.getByTestId("ws3-sympy-matched-alert")).toBeTruthy();

    // Mark VERIFIED while rights_status is STILL PENDING_REVIEW -> Publish & Approve MUST stay disabled
    fireEvent.click(screen.getByTestId("ws3-mark-verified-btn"));
    expect((screen.getByTestId("ws3-approve-btn") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("ws3-publish-btn") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("ws3-publish-reasons").textContent).toContain("PENDING_REVIEW");

    // Check RESTRICTED & TAKEDOWN -> remain strictly disabled even while VERIFIED
    fireEvent.change(screen.getByTestId("ws3-rights-select"), { target: { value: "RESTRICTED" } });
    expect((screen.getByTestId("ws3-publish-btn") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("ws3-approve-btn") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByTestId("ws3-rights-select"), { target: { value: "TAKEDOWN" } });
    expect((screen.getByTestId("ws3-publish-btn") as HTMLButtonElement).disabled).toBe(true);

    // Grant OWNED + keep VERIFIED + no dispute -> Approve & Publish unlock!
    fireEvent.change(screen.getByTestId("ws3-rights-select"), { target: { value: "OWNED" } });
    expect((screen.getByTestId("ws3-approve-btn") as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByTestId("ws3-publish-btn") as HTMLButtonElement).disabled).toBe(false);

    // Revert verification_status to AUTO_VERIFIED -> locks again!
    fireEvent.change(screen.getByTestId("ws3-verification-select"), { target: { value: "AUTO_VERIFIED" } });
    expect((screen.getByTestId("ws3-publish-btn") as HTMLButtonElement).disabled).toBe(true);

    // Re-verify and click Publish -> calls API cleanly
    fireEvent.change(screen.getByTestId("ws3-verification-select"), { target: { value: "VERIFIED" } });
    fireEvent.click(screen.getByTestId("ws3-publish-btn"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });
});

describe("Workspace 4: VietJack Block Studio & Question Authoring (TASK-26)", () => {
  it("supports slash commands (/congthuc, /phuongphap, /chuy, /vidu), Desktop/Mobile preview, and SafeKatexText", () => {
    const { container } = render(<StudioContentEditorShell contentId="cong-thuc-bayes" />);

    expect(screen.getByTestId("ws4-content-studio-layout")).toBeTruthy();
    expect(container.querySelectorAll('[data-katex-rendered="true"]').length).toBeGreaterThan(0);

    const slashInput = screen.getByTestId("ws4-slash-command-input") as HTMLInputElement;
    const addSlash = (cmd: string) => {
      fireEvent.change(slashInput, { target: { value: cmd } });
      fireEvent.click(screen.getByTestId("ws4-run-slash-btn"));
    };

    addSlash("/congthuc");
    addSlash("/phuongphap");
    addSlash("/chuy");
    addSlash("/vidu");

    expect(screen.getAllByTestId("ws4-preview-block-FORMULA_BOX").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByTestId("ws4-preview-block-METHOD_STEPS").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId("ws4-preview-block-TIP_WARNING")).toBeTruthy();
    expect(screen.getByTestId("ws4-preview-block-WORKED_EXAMPLE")).toBeTruthy();

    // Viewport switch Desktop -> Mobile
    fireEvent.click(screen.getByTestId("ws4-viewport-mobile"));
    expect(screen.getByTestId("ws4-preview-viewport").getAttribute("data-viewport-mode")).toBe("MOBILE");
  });

  it("blocks invalid KaTeX, javascript:/data: URLs, unsafe LaTeX, and cross-tenant assets via Pre-Submit Validator", () => {
    const { container } = render(<StudioContentEditorShell contentId="cong-thuc-bayes" />);
    const saveBtn = screen.getByTestId("ws4-save-draft-btn") as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(false);

    const block1Textarea = screen.getByTestId("ws4-block-textarea-1") as HTMLTextAreaElement;

    // 1) Unclosed KaTeX delimiter
    fireEvent.change(block1Textarea, { target: { value: "Công thức chưa đóng $x + y" } });
    expect(saveBtn.disabled).toBe(true);
    expect(screen.getByTestId("ws4-issue-UNCLOSED_KATEX_INLINE")).toBeTruthy();

    // 2) javascript: and data: URLs blocked cleanly
    fireEvent.change(block1Textarea, { target: { value: "Xem [tại đây](javascript:alert(1))" } });
    expect(saveBtn.disabled).toBe(true);
    expect(screen.getByTestId("ws4-issue-UNSAFE_URL_SCHEME")).toBeTruthy();

    fireEvent.change(block1Textarea, { target: { value: "![anh](data:text/html;base64,PHNjcmlwdD4=)" } });
    expect(saveBtn.disabled).toBe(true);
    expect(screen.getByTestId("ws4-issue-UNSAFE_URL_SCHEME")).toBeTruthy();

    // 3) Unsafe LaTeX command inside math delimiter blocked by BOTH linter and SafeKatexText
    fireEvent.change(block1Textarea, { target: { value: "$\\href{javascript:alert(2)}{bam}$" } });
    expect(saveBtn.disabled).toBe(true);
    expect(screen.getAllByTestId("ws4-issue-UNSAFE_LATEX_COMMAND").length).toBeGreaterThan(0);
    expect(container.querySelector('[data-katex-blocked="true"]')).toBeTruthy();

    // 4) Restore safe text and attach cross-tenant asset -> CROSS_TENANT_ASSET_FORBIDDEN
    fireEvent.change(block1Textarea, { target: { value: "Công thức an toàn $P(A) = 0.5$." } });
    expect(saveBtn.disabled).toBe(false);

    fireEvent.click(screen.getByTestId("ws4-asset-toggle-1-asset-other-tenant"));
    expect(saveBtn.disabled).toBe(true);
    expect(screen.getByTestId("ws4-issue-CROSS_TENANT_ASSET_FORBIDDEN")).toBeTruthy();

    // Remove cross-tenant asset -> valid again
    fireEvent.click(screen.getByTestId("ws4-asset-toggle-1-asset-other-tenant"));
    expect(saveBtn.disabled).toBe(false);
  });

  it("renders /studio/questions/new C01 Question Authoring with Pre-Submit Validator and slash solution blocks", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: "q-new-1" }), { status: 201 }));
    render(<QuestionAuthoringShell fetchFn={fetchMock as unknown as typeof fetch} />);

    const submitBtn = screen.getByTestId("ws4-submit-question-btn") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(false);

    // Invalid javascript: payload in stem -> disabled
    const stemInput = screen.getByTestId("ws4-question-stem-input") as HTMLTextAreaElement;
    const validStem = stemInput.value;
    fireEvent.change(stemInput, { target: { value: "Bấm [link](javascript:alert(9)) để xem hình" } });
    expect(submitBtn.disabled).toBe(true);
    expect(screen.getByTestId("ws4-q-issue-UNSAFE_URL_SCHEME")).toBeTruthy();

    // Restore stem + add /chuy solution block
    fireEvent.change(stemInput, { target: { value: validStem } });
    fireEvent.change(screen.getByTestId("ws4-slash-command-input"), { target: { value: "/chuy" } });
    fireEvent.click(screen.getByTestId("ws4-run-slash-btn"));
    expect(submitBtn.disabled).toBe(false);

    fireEvent.click(submitBtn);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId("ws4-question-submit-feedback").textContent).toContain("MATH12-BAYES-NEW-01");
  });
});

describe("Workspace 3 & 4 Next.js Routes & Mandatory 6 UI States", () => {
  it("resolves /studio/imports/[run_id]/review, /studio/content/[id], and /studio/questions/new pages cleanly", async () => {
    const ws3El = await ImportReviewPage({
      params: Promise.resolve({ run_id: "run-w5-01" }),
      searchParams: Promise.resolve({}),
    });
    render(ws3El);
    expect(screen.getByTestId("ws3-split-layout")).toBeTruthy();
    cleanup();

    const ws4ContentEl = await StudioContentPage({
      params: Promise.resolve({ id: "cong-thuc-bayes" }),
      searchParams: Promise.resolve({}),
    });
    render(ws4ContentEl);
    expect(screen.getByTestId("ws4-content-studio-layout")).toBeTruthy();
    cleanup();

    render(<NewQuestionStudioPage />);
    expect(screen.getByTestId("ws4-question-studio-layout")).toBeTruthy();
  });

  it("enforces all 6 mandatory UI states including fail-closed LockedProPreview (no rawAssetUrl leak)", () => {
    const proSession = {
      ...createDemoImportReviewSession("pro-run"),
      requiresProAsset: true,
      rawAssetUrl: "https://cdn.internal/secret-raw-pro-file.pdf",
      previewPdfUrl: "https://cdn.public/preview-2pages.pdf",
    };

    const { container, unmount } = render(
      <BboxReviewerShell
        runId="pro-run"
        initialSession={proSession}
        initialUserTier="FREE"
      />,
    );
    expect(screen.getByTestId("workspace-locked-pro-preview")).toBeTruthy();
    expect(container.innerHTML).not.toContain("secret-raw-pro-file.pdf");
    unmount();

    const states: Array<{ overrides: Record<string, unknown>; testId: string }> = [
      { overrides: { isLoading: true }, testId: "workspace-loading-skeleton" },
      { overrides: { itemCount: 0 }, testId: "workspace-empty-state" },
      { overrides: { fatalErrorCode: "E_STUDIO_500" }, testId: "workspace-error-boundary" },
      { overrides: { isOffline: true, pendingAutosaveCount: 2 }, testId: "workspace-offline-retry-banner" },
      { overrides: { isSequenceConflict409: true }, testId: "workspace-sequence-conflict-modal" },
    ];

    for (const s of states) {
      const view = render(
        <StudioContentEditorShell contentId="cong-thuc-bayes" initialUiOverrides={s.overrides} />,
      );
      expect(screen.getByTestId(s.testId)).toBeTruthy();
      view.unmount();
    }
  });
});
