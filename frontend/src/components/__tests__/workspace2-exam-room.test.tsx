// @vitest-environment jsdom

import "fake-indexeddb/auto";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExamRoomPage from "@/app/phong-thi/[attempt_id]/page";
import { ExamRoomShell } from "@/components/exam-room/ExamRoomShell";
import { createDemoExamAttempt } from "@/components/exam-room/types";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import { ErrorBoundary } from "@/components/workspace/WorkspaceStateViews";
import { createExamAutosaveStore } from "@/lib/exam-autosave-store";
import { ServerClock } from "@/lib/server-clock";

afterEach(() => {
  cleanup();
});

function createTestAttempt(attemptId = "att-test-1") {
  const clock = new ServerClock({
    wallNowMs: () => 1_700_000_000_000,
    monotonicNowMs: () => 1_000,
  });
  clock.recordSample({
    t0MonotonicMs: 990,
    t3MonotonicMs: 1_000,
    t1ServerRecvMs: 1_700_000_000_000,
    t2ServerSendMs: 1_700_000_000_000,
  });
  const base = createDemoExamAttempt(attemptId);
  return {
    clock,
    attempt: {
      ...base,
      deadlineAtIso: new Date(1_700_000_000_000 + 45 * 60 * 1000).toISOString(),
    },
  };
}

describe("Workspace UI 6 Mandatory States & KaTeX Security", () => {
  it("renders LoadingSkeleton, EmptyState, and ErrorBoundary states cleanly", () => {
    const { attempt, clock } = createTestAttempt();

    const loadingView = render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        clock={clock}
        initialUiOverrides={{ isLoading: true }}
      />,
    );
    expect(screen.getByTestId("workspace-loading-skeleton")).toBeTruthy();
    loadingView.unmount();

    const emptyView = render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={{ ...attempt, questions: [] }}
        clock={clock}
      />,
    );
    expect(screen.getByTestId("workspace-empty-state")).toBeTruthy();
    emptyView.unmount();

    const errorView = render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        clock={clock}
        initialUiOverrides={{ fatalErrorCode: "HTTP_500" }}
      />,
    );
    expect(screen.getByTestId("workspace-error-boundary").textContent).toContain("HTTP_500");
    errorView.unmount();
  });

  it("captures runtime component throws in ErrorBoundary without crashing shell", () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    function CrashChild(): React.ReactElement {
      throw new Error("boom-render");
    }

    render(
      <ErrorBoundary>
        <CrashChild />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("workspace-error-boundary").textContent).toContain("boom-render");
    consoleSpy.mockRestore();
  });

  it("renders LockedProPreview for FREE users and NEVER leaks rawAssetUrl into DOM", () => {
    const { attempt, clock } = createTestAttempt();
    const secretUrl = "https://storage.internal/private/secret-full-exam-key.pdf?sig=xyz";

    const { container } = render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={{
          ...attempt,
          userTier: "FREE",
          requiresProAsset: true,
          previewPdfUrl: "/api/v1/assets/preview-2pages.pdf",
          rawAssetUrl: secretUrl,
        }}
        clock={clock}
      />,
    );

    const panel = screen.getByTestId("workspace-locked-pro-preview");
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain("Nâng cấp gói Pro");
    expect(container.innerHTML).not.toContain("secret-full-exam-key.pdf");
    expect(container.innerHTML).toContain("preview-2pages.pdf");
  });

  it("keeps exam inputs fully interactive when OfflineRetryBanner is active", () => {
    const { attempt, clock } = createTestAttempt();

    render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        clock={clock}
        initialUiOverrides={{ isOffline: true, pendingAutosaveCount: 2 }}
      />,
    );

    expect(screen.getByTestId("workspace-offline-retry-banner")).toBeTruthy();
    const radioButtons = screen.getAllByRole("radio");
    expect(radioButtons[0]?.hasAttribute("disabled")).toBe(false);
    fireEvent.click(radioButtons[0]!);
    expect(radioButtons[0]?.getAttribute("aria-checked")).toBe("true");
  });

  it("hardens KaTeX against javascript: URLs, DOM injection macros, and invalid LaTeX", () => {
    const { container } = render(
      <div>
        <SafeKatexText text={"Giải phương trình $x^2 - 4 = 0$"} />
        <SafeKatexText text={"Độc hại $\\href{javascript:alert(1)}{click}$"} />
        <SafeKatexText text={"DOM $\\htmlClass{evil}{x}$"} />
        <SafeKatexText text={"Lỗi cú pháp $\\frac{1$"} />
      </div>,
    );

    expect(container.querySelectorAll('[data-katex-rendered="true"]').length).toBeGreaterThanOrEqual(2);
    expect(container.querySelectorAll('[data-katex-blocked="true"]').length).toBe(2);
    expect(container.innerHTML).not.toContain("javascript:");
    expect(container.innerHTML).not.toContain("evil");
  });
});

describe("Workspace 2 THPTQG 3-Part Exam Interactions, Autosave 409 & Submit", () => {
  it("handles Part I selection + strike-through + keyboard shortcuts + flag + navigation", async () => {
    const { attempt, clock } = createTestAttempt("att-p1");
    const { container } = render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        clock={clock}
      />,
    );

    // Click option A
    const radioButtons = screen.getAllByRole("radio");
    fireEvent.click(radioButtons[0]!);
    expect(radioButtons[0]?.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("palette-btn-1").getAttribute("data-status")).toBe("answered");

    // Eliminate option A -> clears selection & marks eliminated
    fireEvent.click(screen.getByTestId("eliminate-A"));
    expect(screen.getByTestId("part1-option-A").getAttribute("data-eliminated")).toBe("true");
    expect(radioButtons[0]?.getAttribute("aria-checked")).toBe("false");

    // Keyboard shortcut B selects B; F flags question
    fireEvent.keyDown(window, { key: "b" });
    expect(radioButtons[1]?.getAttribute("aria-checked")).toBe("true");

    fireEvent.keyDown(window, { key: "f" });
    expect(screen.getByTestId("flag-question-btn").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("palette-flag-badge-1")).toBeTruthy();

    // Verify rendered math inside Part I stem
    expect(container.querySelector('[data-katex-rendered="true"]')).toBeTruthy();

    // Navigate to question 2 via keyboard ArrowRight
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByTestId("question-card-q-p1-2")).toBeTruthy();
  });

  it("warns in yellow for incomplete Part II True/False cluster and clears when all 4 answered", () => {
    const { attempt, clock } = createTestAttempt("att-p2");
    render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        clock={clock}
      />,
    );

    // Switch to Part II (question 3)
    fireEvent.click(screen.getByTestId("palette-btn-3"));
    expect(screen.queryByTestId("part2-partial-warning")).toBeNull();

    // Answer 2 of 4 statements -> triggers yellow partial warning + palette badge
    fireEvent.click(screen.getByTestId("tf-a-true"));
    fireEvent.click(screen.getByTestId("tf-b-false"));
    expect(screen.getByTestId("part2-partial-warning").textContent).toContain("2/4");
    expect(screen.getByTestId("palette-btn-3").getAttribute("data-status")).toBe("partial");

    // Finish remaining c & d -> clears warning & marks complete
    fireEvent.click(screen.getByTestId("tf-c-true"));
    fireEvent.click(screen.getByTestId("tf-d-false"));
    expect(screen.queryByTestId("part2-partial-warning")).toBeNull();
    expect(screen.getByTestId("palette-btn-3").getAttribute("data-status")).toBe("complete");
  });

  it("enforces Part III 4-cell policy and warns in red on 1.000 thousands separator or >4 chars", () => {
    const { attempt, clock } = createTestAttempt("att-p3");
    render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        clock={clock}
      />,
    );

    fireEvent.click(screen.getByTestId("palette-btn-4"));
    const input = screen.getByTestId("part3-short-input") as HTMLInputElement;

    // 1.000 thousands separator warning
    fireEvent.change(input, { target: { value: "1.000" } });
    expect(screen.getByTestId("part3-validation-warning").textContent).toContain("1.000");

    // > 4 characters warning
    fireEvent.change(input, { target: { value: "12345" } });
    expect(screen.getByTestId("part3-validation-warning").textContent).toContain("4");

    // Valid negative decimal -2,5 populates 4-cell grid & clears warning
    fireEvent.change(input, { target: { value: "-2,5" } });
    expect(screen.queryByTestId("part3-validation-warning")).toBeNull();
    expect(screen.getByTestId("part3-cell-0").textContent).toBe("-");
    expect(screen.getByTestId("part3-cell-1").textContent).toBe("2");
    expect(screen.getByTestId("part3-cell-2").textContent).toBe(",");
    expect(screen.getByTestId("part3-cell-3").textContent).toBe("5");
    expect(screen.getByTestId("palette-btn-4").getAttribute("data-status")).toBe("answered");
  });

  it("triggers SequenceConflictModal on persistent 409 STALE_CLIENT_SEQUENCE and resolves keep_mine", async () => {
    const { attempt, clock } = createTestAttempt("att-409");
    let putCount = 0;

    const mockFetch: typeof fetch = vi.fn(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/sync")) {
        return new Response(
          JSON.stringify({
            responses: {
              "q-p1-1": {
                revision_no: 4,
                current_sequence: 4,
                last_idempotency_key: "other-device-key",
                response_payload: { kind: "PART_1", choice: "D" },
              },
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/responses/")) {
        putCount += 1;
        // Fail first 2 PUT attempts with 409 to trigger real persistent conflict modal
        if (putCount <= 2) {
          return new Response(
            JSON.stringify({
              error_code: "STALE_CLIENT_SEQUENCE",
              current_sequence: 4,
              revision_no: 4,
              server_payload: { kind: "PART_1", choice: "D" },
            }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(
          JSON.stringify({ status: "COMMITTED", saved_response_version: 5 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("{}", { status: 200 });
    });

    const store = createExamAutosaveStore({
      dbName: `ulp_test_409_${Date.now()}`,
      fetchFn: mockFetch,
      autoAttachWindowListeners: false,
    });

    render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        autosaveStore={store}
        clock={clock}
        fetchFn={mockFetch}
      />,
    );

    // Select option A -> enqueues & flushes -> 409 x 2 -> Conflict modal appears
    const radioButtons = screen.getAllByRole("radio");
    fireEvent.click(radioButtons[0]!);

    await waitFor(() => {
      expect(screen.getByTestId("workspace-sequence-conflict-modal")).toBeTruthy();
    });

    // Resolve by keeping local answer
    fireEvent.click(screen.getByText("Giữ bài làm trên máy này"));

    await waitFor(() => {
      expect(screen.queryByTestId("workspace-sequence-conflict-modal")).toBeNull();
    });

    await store.close();
  });

  it("completes idempotent submit barrier and displays server receipt", async () => {
    const { attempt, clock } = createTestAttempt("att-submit");
    const mockFetch: typeof fetch = vi.fn(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/responses/")) {
        return new Response(
          JSON.stringify({ status: "COMMITTED", saved_response_version: 1 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/submit")) {
        return new Response(
          JSON.stringify({
            status: "submitted",
            receipt_id: "RCPT-2025-999",
            submitted_at_server_iso: "2026-11-14T08:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("{}", { status: 200 });
    });

    const store = createExamAutosaveStore({
      dbName: `ulp_test_submit_${Date.now()}`,
      fetchFn: mockFetch,
      autoAttachWindowListeners: false,
    });

    render(
      <ExamRoomShell
        attemptId={attempt.attemptId}
        initialAttempt={attempt}
        autosaveStore={store}
        clock={clock}
        fetchFn={mockFetch}
      />,
    );

    fireEvent.click(screen.getByTestId("open-submit-modal-btn"));
    expect(screen.getByTestId("exam-submit-modal")).toBeTruthy();

    fireEvent.click(screen.getByTestId("confirm-submit-btn"));
    await waitFor(() => {
      const receipt = screen.getByTestId("exam-submitted-receipt");
      expect(receipt.textContent).toContain("RCPT-2025-999");
    });

    await store.close();
  });

  it("renders /phong-thi/[attempt_id] Next.js dynamic route entry in demo mode", async () => {
    const element = await ExamRoomPage({
      params: Promise.resolve({ attempt_id: "demo-attempt" }),
      searchParams: Promise.resolve({ demo: "1" }),
    });
    render(element);
    expect(screen.getByText(/H2Edu/)).toBeTruthy();
    expect(screen.getByTestId("exam-question-palette")).toBeTruthy();
  });
});
