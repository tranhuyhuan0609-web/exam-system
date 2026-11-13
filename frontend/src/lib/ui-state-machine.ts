/**
 * V11.3 Chapter 3.8B mandatory 6-state UI resolver across all 6 frontend Workspaces:
 * LoadingSkeleton, EmptyState, ErrorBoundary, LockedProPreview,
 * OfflineRetryBanner, SequenceConflictModal (+ Ready state).
 */

export type WorkspaceUiStateKind =
  | "LoadingSkeleton"
  | "EmptyState"
  | "ErrorBoundary"
  | "LockedProPreview"
  | "OfflineRetryBanner"
  | "SequenceConflictModal"
  | "Ready";

export type UserTier = "FREE" | "STUDENT_PRO" | "TEACHER_PRO" | "SCHOOL_ENTERPRISE";

export interface WorkspaceUiInput {
  readonly isLoading: boolean;
  readonly fatalErrorCode?: string | null;
  readonly isSequenceConflict409?: boolean;
  readonly isOffline?: boolean;
  readonly pendingAutosaveCount?: number;
  readonly itemCount?: number;
  readonly requiresProAsset?: boolean;
  readonly userTier?: UserTier;
  readonly previewPdfUrl?: string | null;
  readonly rawAssetUrl?: string | null;
}

export interface ResolvedWorkspaceUiState {
  readonly kind: WorkspaceUiStateKind;
  readonly interactiveExamAllowed: boolean;
  readonly showUpgradeCta: boolean;
  readonly requiresServerSyncModal: boolean;
  readonly exposedAssetUrl: string | null;
  readonly bannerMessage: string | null;
}

function hasProAccess(tier: UserTier | undefined): boolean {
  return (
    tier === "STUDENT_PRO" ||
    tier === "TEACHER_PRO" ||
    tier === "SCHOOL_ENTERPRISE"
  );
}

/**
 * Resolves the active UI state with fail-closed security guarantees:
 * 1. Conflict Modal (`409 STALE_CLIENT_SEQUENCE`) takes precedence for attempt safety.
 * 2. Offline exam sessions NEVER lock input (`interactiveExamAllowed: true`).
 * 3. `FREE` tier viewing Pro assets NEVER exposes `rawAssetUrl` to DOM state.
 */
export function resolveWorkspaceUiState(
  input: WorkspaceUiInput,
): ResolvedWorkspaceUiState {
  if (input.isSequenceConflict409) {
    return {
      kind: "SequenceConflictModal",
      interactiveExamAllowed: false,
      showUpgradeCta: false,
      requiresServerSyncModal: true,
      exposedAssetUrl: null,
      bannerMessage:
        "Phát hiện tab hoặc thiết bị khác đã cập nhật bài làm. Đồng bộ trạng thái mới nhất từ máy chủ.",
    };
  }

  if (input.fatalErrorCode) {
    return {
      kind: "ErrorBoundary",
      interactiveExamAllowed: false,
      showUpgradeCta: false,
      requiresServerSyncModal: false,
      exposedAssetUrl: null,
      bannerMessage: `Đã xảy ra sự cố hiển thị (${input.fatalErrorCode}).`,
    };
  }

  if (input.isLoading) {
    return {
      kind: "LoadingSkeleton",
      interactiveExamAllowed: false,
      showUpgradeCta: false,
      requiresServerSyncModal: false,
      exposedAssetUrl: null,
      bannerMessage: null,
    };
  }

  const lockedPro =
    Boolean(input.requiresProAsset) && !hasProAccess(input.userTier);

  if (lockedPro) {
    return {
      kind: "LockedProPreview",
      interactiveExamAllowed: false,
      showUpgradeCta: true,
      requiresServerSyncModal: false,
      // Strict security guard: never leak original Pro URL into DOM props.
      exposedAssetUrl: input.previewPdfUrl ?? null,
      bannerMessage: "Nâng cấp Pro để mở khóa toàn bộ tài liệu gốc và PDF tải về.",
    };
  }

  const allowedUrl = input.rawAssetUrl ?? input.previewPdfUrl ?? null;

  if (input.isOffline) {
    const pending = Math.max(0, input.pendingAutosaveCount ?? 0);
    return {
      kind: "OfflineRetryBanner",
      interactiveExamAllowed: true,
      showUpgradeCta: false,
      requiresServerSyncModal: false,
      exposedAssetUrl: allowedUrl,
      bannerMessage: `Mất kết nối mạng — đang lưu tạm ngoại tuyến (${pending} câu chờ đồng bộ).`,
    };
  }

  if ((input.itemCount ?? 1) === 0) {
    return {
      kind: "EmptyState",
      interactiveExamAllowed: true,
      showUpgradeCta: false,
      requiresServerSyncModal: false,
      exposedAssetUrl: allowedUrl,
      bannerMessage: "Chưa có dữ liệu phù hợp trong mục này.",
    };
  }

  return {
    kind: "Ready",
    interactiveExamAllowed: true,
    showUpgradeCta: false,
    requiresServerSyncModal: false,
    exposedAssetUrl: allowedUrl,
    bannerMessage: null,
  };
}
