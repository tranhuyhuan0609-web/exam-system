import { describe, expect, it } from "vitest";
import { resolveWorkspaceUiState } from "../ui-state-machine";
import {
  evaluateDrivePairingCandidate,
  evaluateOutboxQueueHealth,
  evaluateParserDriftStatus,
  validateAdminMutationReason,
} from "../workspace6-admin-ops";

describe("Workspace UI 6-state resolver", () => {
  it("never exposes raw Pro asset URL when user tier is FREE", () => {
    const state = resolveWorkspaceUiState({
      isLoading: false,
      requiresProAsset: true,
      userTier: "FREE",
      previewPdfUrl: "https://cdn.h2edu.vn/previews/doc_p2.pdf",
      rawAssetUrl: "https://cdn.h2edu.vn/private/full_secret.pdf",
    });

    expect(state.kind).toBe("LockedProPreview");
    expect(state.showUpgradeCta).toBe(true);
    expect(state.exposedAssetUrl).toBe("https://cdn.h2edu.vn/previews/doc_p2.pdf");
    expect(state.exposedAssetUrl).not.toContain("full_secret.pdf");
  });

  it("keeps exam input interactive while showing OfflineRetryBanner", () => {
    const state = resolveWorkspaceUiState({
      isLoading: false,
      isOffline: true,
      requiresProAsset: false,
      pendingAutosaveCount: 7,
      itemCount: 22,
    });

    expect(state.kind).toBe("OfflineRetryBanner");
    expect(state.interactiveExamAllowed).toBe(true);
    expect(state.bannerMessage).toContain("7");
  });

  it("escalates 409 STALE_CLIENT_SEQUENCE to SequenceConflictModal above all other states", () => {
    const state = resolveWorkspaceUiState({
      isLoading: false,
      isOffline: true,
      isSequenceConflict409: true,
      pendingAutosaveCount: 3,
    });

    expect(state.kind).toBe("SequenceConflictModal");
    expect(state.requiresServerSyncModal).toBe(true);
    expect(state.interactiveExamAllowed).toBe(false);
  });

  it("covers LoadingSkeleton, ErrorBoundary, EmptyState, and Ready", () => {
    expect(resolveWorkspaceUiState({ isLoading: true }).kind).toBe("LoadingSkeleton");
    expect(
      resolveWorkspaceUiState({ isLoading: false, fatalErrorCode: "HTTP_500" }).kind,
    ).toBe("ErrorBoundary");
    expect(resolveWorkspaceUiState({ isLoading: false, itemCount: 0, requiresProAsset: false }).kind).toBe(
      "EmptyState",
    );
    expect(
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: 10,
        requiresProAsset: true,
        userTier: "STUDENT_PRO",
        rawAssetUrl: "https://signed.example/full.pdf",
      }),
    ).toMatchObject({
      kind: "Ready",
      exposedAssetUrl: "https://signed.example/full.pdf",
      showUpgradeCta: false,
    });
  });
});

describe("Workspace 6 Admin Ops policies", () => {
  it("enforces mandatory >= 15 character audit reason for sensitive actions", () => {
    expect(validateAdminMutationReason("EXECUTE_TAKEDOWN", "   ").errorCode).toBe(
      "REASON_REQUIRED",
    );
    expect(validateAdminMutationReason("OVERRIDE_GRADE", "sai de").errorCode).toBe(
      "REASON_TOO_SHORT",
    );

    const valid = validateAdminMutationReason(
      "CHANGE_RIGHTS_STATUS",
      "Xác nhận giấy phép NXB ngày 13/11/2026 hợp lệ.",
    );
    expect(valid.ok).toBe(true);
    expect(valid.errorCode).toBeNull();
    expect(valid.charLength).toBeGreaterThanOrEqual(15);
  });

  it("scores 4-signal Drive Đề+HDC pairing accurately", () => {
    const strong = evaluateDrivePairingCandidate({
      filenameSimilarity: 0.96,
      sameFolder: true,
      titleOverlap: 0.9,
      examQuestionCount: 22,
      keyQuestionCount: 22,
    });
    expect(strong.highConfidence).toBe(true);
    expect(strong.canConfirm).toBe(true);
    expect(strong.warningCodes).toEqual([]);

    const weak = evaluateDrivePairingCandidate({
      filenameSimilarity: 0.3,
      sameFolder: false,
      titleOverlap: 0.2,
      examQuestionCount: 28,
      keyQuestionCount: 22,
    });
    expect(weak.highConfidence).toBe(false);
    expect(weak.canConfirm).toBe(false);
    expect(weak.warningCodes).toContain("DIFFERENT_FOLDER");
    expect(weak.warningCodes).toContain("QUESTION_COUNT_MISMATCH");
  });

  it("detects A02 ParserDriftMonitor auto-pause (>15% empty across 30+ pages) and Outbox SLO breach (>90s)", () => {
    expect(evaluateParserDriftStatus(5, 30)).toMatchObject({
      status: "PAUSED_DRIFT",
      shouldAutoPause: true,
    });
    expect(evaluateParserDriftStatus(4, 30)).toMatchObject({
      status: "HEALTHY",
      shouldAutoPause: false,
    });
    expect(evaluateParserDriftStatus(3, 10)).toMatchObject({
      status: "WATCH",
      shouldAutoPause: false,
    });

    expect(evaluateOutboxQueueHealth(95, 2)).toMatchObject({
      status: "SLO_BREACH",
      alertCodes: ["INGESTION_QUEUE_SLO_BREACH", "DLQ_NON_EMPTY"],
    });
    expect(evaluateOutboxQueueHealth(45, 0)).toMatchObject({
      status: "OK",
      alertCodes: [],
    });
  });
});
