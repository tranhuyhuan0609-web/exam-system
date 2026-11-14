"use client";

import React, { Component, type ErrorInfo, type ReactNode } from "react";
import { resolveWorkspaceUiState, type UserTier } from "@/lib/ui-state-machine";

/* -------------------------------------------------------------------------- */
/* 1. LoadingSkeleton                                                         */
/* -------------------------------------------------------------------------- */

export interface LoadingSkeletonProps {
  title?: string;
}

export function LoadingSkeleton({ title = "Đang tải phòng thi H2Edu..." }: LoadingSkeletonProps) {
  return (
    <div
      role="status"
      aria-label={title}
      data-testid="workspace-loading-skeleton"
      className="mx-auto max-w-6xl space-y-6 p-4 md:p-6"
    >
      <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="h-6 w-48 animate-pulse rounded bg-slate-200" />
        <div className="flex items-center gap-3">
          <div className="h-8 w-28 animate-pulse rounded-lg bg-slate-200" />
          <div className="h-8 w-24 animate-pulse rounded-lg bg-blue-100" />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm lg:col-span-8">
          <div className="h-5 w-32 animate-pulse rounded bg-slate-200" />
          <div className="h-20 w-full animate-pulse rounded-lg bg-slate-100" />
          <div className="space-y-3 pt-2">
            {[0, 1, 2, 3].map((idx) => (
              <div key={idx} className="h-12 w-full animate-pulse rounded-lg bg-slate-100" />
            ))}
          </div>
        </div>
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-4">
          <div className="h-5 w-36 animate-pulse rounded bg-slate-200" />
          <div className="grid grid-cols-5 gap-2">
            {Array.from({ length: 15 }).map((_, idx) => (
              <div key={idx} className="h-10 animate-pulse rounded-lg bg-slate-100" />
            ))}
          </div>
        </div>
      </div>
      <p className="sr-only">{title}</p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 2. EmptyState                                                              */
/* -------------------------------------------------------------------------- */

export interface EmptyStateProps {
  title?: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function EmptyState({
  title = "Chưa có câu hỏi nào trong phiên thi",
  description = "Đề thi hiện chưa có nội dung hiển thị hoặc phiên thi chưa được khởi tạo câu hỏi.",
  actionLabel = "Tải lại đề thi",
  onAction,
}: EmptyStateProps) {
  return (
    <div
      data-testid="workspace-empty-state"
      className="mx-auto my-12 max-w-lg rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm"
    >
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      </div>
      <h2 className="mt-4 text-lg font-semibold text-slate-900">{title}</h2>
      <p className="mt-2 text-sm text-slate-600">{description}</p>
      {onAction && (
        <button
          type="button"
          onClick={onAction}
          className="mt-5 inline-flex items-center rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-800"
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 3. ErrorBoundary                                                           */
/* -------------------------------------------------------------------------- */

export interface ErrorBoundaryProps {
  children?: ReactNode;
  errorCode?: string | null;
  errorMessage?: string | null;
  onRetry?: () => void;
}

interface ErrorBoundaryState {
  caughtError: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { caughtError: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { caughtError: error };
  }

  componentDidCatch(_error: Error, _errorInfo: ErrorInfo): void {
    // Error captured for clean UI fallback without crashing Workspace shell.
  }

  private handleRetry = (): void => {
    this.setState({ caughtError: null });
    this.props.onRetry?.();
  };

  render(): ReactNode {
    const activeError = this.props.errorCode || this.props.errorMessage || this.state.caughtError;
    if (activeError) {
      const displayCode = this.props.errorCode ?? "RENDER_ERROR";
      const displayMsg =
        this.props.errorMessage ??
        this.state.caughtError?.message ??
        "Không thể tải hoặc hiển thị nội dung phòng thi.";

      return (
        <div
          role="alert"
          data-testid="workspace-error-boundary"
          className="mx-auto my-12 max-w-lg rounded-2xl border border-red-200 bg-red-50/60 p-8 text-center shadow-sm"
        >
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-700">
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
          </div>
          <h2 className="mt-4 text-lg font-bold text-red-900">Đã xảy ra sự cố ({displayCode})</h2>
          <p className="mt-2 text-sm text-red-800">{displayMsg}</p>
          <button
            type="button"
            onClick={this.handleRetry}
            className="mt-5 inline-flex items-center rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-800"
          >
            Thử tải lại
          </button>
        </div>
      );
    }

    return this.props.children ?? null;
  }
}

/* -------------------------------------------------------------------------- */
/* 4. LockedProPreview                                                        */
/* -------------------------------------------------------------------------- */

export interface LockedProPreviewProps {
  title?: string;
  userTier?: UserTier;
  requiresProAsset?: boolean;
  previewPdfUrl?: string | null;
  rawAssetUrl?: string | null;
  onUpgradeClick?: () => void;
}

export function LockedProPreview({
  title = "Nội dung đề thi dành riêng cho tài khoản H2Edu Pro",
  userTier = "FREE",
  requiresProAsset = true,
  previewPdfUrl = null,
  rawAssetUrl = null,
  onUpgradeClick,
}: LockedProPreviewProps) {
  // Strictly resolve URL exposure through fail-closed UI state machine so rawAssetUrl NEVER enters DOM.
  const resolved = resolveWorkspaceUiState({
    isLoading: false,
    requiresProAsset,
    userTier,
    previewPdfUrl,
    rawAssetUrl,
  });

  return (
    <div
      data-testid="workspace-locked-pro-preview"
      className="mx-auto my-10 max-w-2xl rounded-2xl border border-amber-200 bg-white p-8 shadow-sm"
    >
      <div className="inline-flex items-center gap-2 rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">
        Gói Pro • Bảo mật tài liệu
      </div>
      <h2 className="mt-4 text-xl font-bold text-slate-900">{title}</h2>
      <p className="mt-2 text-sm text-slate-600">
        {resolved.bannerMessage ??
          "Nâng cấp Pro để mở khóa toàn bộ đề thi, đáp án chi tiết và tài liệu gốc."}
      </p>

      {resolved.exposedAssetUrl && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <p className="text-xs font-medium text-slate-700">Bản xem trước (tối đa 2 trang đầu):</p>
          <a
            href={resolved.exposedAssetUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-block text-sm font-semibold text-blue-700 underline"
          >
            Xem bản PDF xem trước
          </a>
        </div>
      )}

      {resolved.showUpgradeCta && (
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onUpgradeClick}
            className="inline-flex items-center rounded-lg bg-amber-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-amber-700"
          >
            Nâng cấp gói Pro
          </button>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 5. OfflineRetryBanner                                                      */
/* -------------------------------------------------------------------------- */

export interface OfflineRetryBannerProps {
  pendingCount: number;
  message?: string | null;
  onRetrySync?: () => void;
  isRetrying?: boolean;
}

export function OfflineRetryBanner({
  pendingCount,
  message,
  onRetrySync,
  isRetrying = false,
}: OfflineRetryBannerProps) {
  return (
    <div
      role="status"
      data-testid="workspace-offline-retry-banner"
      className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-950"
    >
      <div className="flex items-center gap-2.5">
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" aria-hidden="true" />
        <span className="font-medium">
          {message ??
            `Mất kết nối mạng — bài làm đang được lưu an toàn trên thiết bị (${pendingCount} câu chờ đồng bộ). Bạn vẫn có thể tiếp tục làm bài bình thường.`}
        </span>
      </div>
      {onRetrySync && (
        <button
          type="button"
          onClick={onRetrySync}
          disabled={isRetrying}
          className="rounded-md border border-amber-400 bg-white px-3 py-1 text-xs font-semibold text-amber-900 transition hover:bg-amber-100 disabled:opacity-60"
        >
          {isRetrying ? "Đang thử đồng bộ..." : "Thử đồng bộ lại"}
        </button>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 6. SequenceConflictModal                                                   */
/* -------------------------------------------------------------------------- */

export interface SequenceConflictItem {
  readonly rowId?: number;
  readonly questionVersionId: string;
  readonly questionLabel?: string;
  readonly localPayload: unknown;
  readonly serverPayload: unknown;
}

export interface SequenceConflictModalProps {
  conflict: SequenceConflictItem;
  onResolve: (resolution: "keep_mine" | "take_server") => void;
  isResolving?: boolean;
}

function formatConflictPayload(payload: unknown): string {
  if (payload === null || payload === undefined) {
    return "Chưa trả lời";
  }
  if (typeof payload === "string") {
    return payload;
  }
  try {
    return JSON.stringify(payload);
  } catch {
    return String(payload);
  }
}

export function SequenceConflictModal({
  conflict,
  onResolve,
  isResolving = false,
}: SequenceConflictModalProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="sequence-conflict-title"
      data-testid="workspace-sequence-conflict-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs"
    >
      <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-6 shadow-xl">
        <div className="inline-flex items-center rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-800">
          409 STALE_CLIENT_SEQUENCE
        </div>
        <h2 id="sequence-conflict-title" className="mt-3 text-lg font-bold text-slate-900">
          Phát hiện xung đột đồng bộ bài làm
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          Phiên thi của bạn đã được cập nhật từ tab hoặc thiết bị khác tại{" "}
          <span className="font-semibold text-slate-900">
            {conflict.questionLabel ?? conflict.questionVersionId}
          </span>
          . Hãy chọn phiên bản bạn muốn giữ lại để tiếp tục.
        </p>

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-blue-200 bg-blue-50/50 p-3">
            <div className="text-xs font-semibold text-blue-900">Trên thiết bị này</div>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-xs text-slate-800">
              {formatConflictPayload(conflict.localPayload)}
            </pre>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="text-xs font-semibold text-slate-700">Dữ liệu trên máy chủ</div>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-xs text-slate-800">
              {formatConflictPayload(conflict.serverPayload)}
            </pre>
          </div>
        </div>

        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            disabled={isResolving}
            onClick={() => onResolve("take_server")}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition hover:bg-slate-50 disabled:opacity-60"
          >
            Lấy dữ liệu từ máy chủ
          </button>
          <button
            type="button"
            disabled={isResolving}
            onClick={() => onResolve("keep_mine")}
            className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-800 disabled:opacity-60"
          >
            Giữ bài làm trên máy này
          </button>
        </div>
      </div>
    </div>
  );
}
