"use client";

import React, { useMemo, useState } from "react";
import { StudioHeader } from "@/components/studio/StudioHeader";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import type { BoundingBox } from "@/lib/bbox-reviewer";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { PdfBboxCanvas } from "./PdfBboxCanvas";
import { QuestionInspectorPanel } from "./QuestionInspectorPanel";
import {
  createDemoImportReviewSession,
  type BboxRegionKind,
  type ImportReviewSessionData,
  type ReviewQuestionItem,
} from "./types";

export interface BboxReviewerShellProps {
  runId: string;
  initialSession?: ImportReviewSessionData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function BboxReviewerShell({
  runId,
  initialSession,
  initialUserTier = "TEACHER_PRO",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: BboxReviewerShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [session, setSession] = useState<ImportReviewSessionData>(
    () => initialSession ?? createDemoImportReviewSession(runId),
  );
  const [selectedItemId, setSelectedItemId] = useState<string>(
    () => (initialSession ?? createDemoImportReviewSession(runId)).items[0]?.id ?? "",
  );
  const [activePageNo, setActivePageNo] = useState<number>(
    () => (initialSession ?? createDemoImportReviewSession(runId)).items[0]?.pageNo ?? 1,
  );
  const [activeRegionKind, setActiveRegionKind] = useState<BboxRegionKind>("QUESTION");
  const [zoomPercent, setZoomPercent] = useState<number>(100);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  const selectedItem = useMemo(
    () => session.items.find((item) => item.id === selectedItemId) ?? session.items[0] ?? null,
    [session.items, selectedItemId],
  );

  const activePage = useMemo(
    () => session.pages.find((p) => p.pageNo === activePageNo) ?? session.pages[0] ?? null,
    [session.pages, activePageNo],
  );

  const pageItems = useMemo(
    () => session.items.filter((item) => item.pageNo === (activePage?.pageNo ?? 1)),
    [session.items, activePage],
  );

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: session.items.length,
        requiresProAsset: session.requiresProAsset,
        userTier,
        previewPdfUrl: session.previewPdfUrl,
        rawAssetUrl: session.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [session, userTier, initialUiOverrides, conflictDismissed],
  );

  const handleSelectItem = (itemId: string) => {
    setSelectedItemId(itemId);
    const target = session.items.find((item) => item.id === itemId);
    if (target) {
      setActivePageNo(target.pageNo);
      setZoomPercent(120);
      if (target.regions.length > 0 && !target.regions.some((r) => r.kind === activeRegionKind)) {
        setActiveRegionKind(target.regions[0]!.kind);
      }
      setActionFeedback(`Đã phóng to trang ${target.pageNo} tại vùng ${target.stableCode}.`);
    }
  };

  const handleUpdateItem = (itemId: string, patch: Partial<ReviewQuestionItem>) => {
    setSession((prev) => ({
      ...prev,
      items: prev.items.map((it) => (it.id === itemId ? { ...it, ...patch } : it)),
    }));
  };

  const handleRecropRegion = (itemId: string, kind: BboxRegionKind, nextBox: BoundingBox) => {
    setSession((prev) => ({
      ...prev,
      items: prev.items.map((item) => {
        if (item.id !== itemId) return item;
        const hasKind = item.regions.some((r) => r.kind === kind);
        const regions = hasKind
          ? item.regions.map((r) => (r.kind === kind ? { ...r, box: nextBox } : r))
          : [
              ...item.regions,
              {
                id: `${item.id}-${kind.toLowerCase()}`,
                kind,
                label: `${item.stableCode} · ${kind}`,
                box: nextBox,
              },
            ];
        return { ...item, regions };
      }),
    }));
    setActionFeedback(
      `Đã cắt lại ${kind} (${nextBox.x0}, ${nextBox.y0}, ${nextBox.x1}, ${nextBox.y1}).`,
    );
  };

  const handleMarkVerified = (itemId: string) => {
    handleUpdateItem(itemId, { verificationStatus: "VERIFIED" });
    setActionFeedback("Đã xác nhận chuyên môn VERIFIED.");
  };

  const postStudioAction = async (path: string, body: unknown, fallbackMessage: string) => {
    setIsSubmitting(true);
    try {
      const caller = fetchFn ?? (typeof fetch === "function" ? fetch : null);
      if (caller && apiBase !== "__OFFLINE__") {
        const res = await caller(`${apiBase}${path}`, {
          method: path.includes("/structure") ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (res.ok) {
          setActionFeedback(fallbackMessage);
          return;
        }
      }
      setActionFeedback(fallbackMessage);
    } catch {
      setActionFeedback(`${fallbackMessage} (lưu cục bộ khi mất kết nối API).`);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ErrorBoundary
      errorCode={
        uiResolution.kind === "ErrorBoundary"
          ? (initialUiOverrides?.fatalErrorCode ?? "STUDIO_REVIEW_ERROR")
          : null
      }
    >
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <StudioHeader activeRoute="ws3-review" userTier={userTier} onTierChange={setUserTier} />

        {uiResolution.kind === "SequenceConflictModal" && (
          <SequenceConflictModal
            conflict={{
              questionVersionId: runId,
              questionLabel: session.documentTitle,
              localPayload: { selectedItemId },
              serverPayload: { status: "UPDATED_BY_PEER" },
            }}
            onResolve={() => setConflictDismissed(true)}
          />
        )}

        {uiResolution.kind === "OfflineRetryBanner" && (
          <OfflineRetryBanner
            pendingCount={initialUiOverrides?.pendingAutosaveCount ?? 0}
            message={uiResolution.bannerMessage}
          />
        )}

        {uiResolution.kind === "LoadingSkeleton" ? (
          <LoadingSkeleton title="Đang tải dữ liệu Bounding-Box Reviewer..." />
        ) : uiResolution.kind === "ErrorBoundary" ? (
          <ErrorBoundary
            errorCode={initialUiOverrides?.fatalErrorCode ?? "STUDIO_REVIEW_ERROR"}
            errorMessage={uiResolution.bannerMessage}
          />
        ) : uiResolution.kind === "LockedProPreview" ? (
          <main data-testid="ws3-locked-pro-container" className="mx-auto max-w-4xl px-4 py-8">
            <LockedProPreview
              title={`Tài liệu bản quyền giới hạn: ${session.documentTitle}`}
              userTier={userTier}
              requiresProAsset={session.requiresProAsset}
              previewPdfUrl={uiResolution.exposedAssetUrl}
              rawAssetUrl={null}
              onUpgradeClick={() => setUserTier("TEACHER_PRO")}
            />
          </main>
        ) : uiResolution.kind === "EmptyState" || !selectedItem || !activePage ? (
          <EmptyState
            title="Chưa có câu hỏi được bóc tách trong phiên nhập"
            description={`Phiên ${runId} chưa có vùng BBox hoặc câu hỏi ứng viên để kiểm duyệt.`}
          />
        ) : (
          <main data-testid="ws3-split-layout" className="mx-auto max-w-[1600px] px-4 py-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-xs">
              <div>
                <h1 className="text-lg font-extrabold text-slate-900">{session.documentTitle}</h1>
                <p className="text-xs text-slate-500">
                  Run ID: <code>{session.runId}</code> · Tệp nguồn: <code>{session.sourceName}</code>
                </p>
              </div>
              <span className="rounded-full bg-indigo-50 px-3 py-1 text-xs font-bold text-indigo-800">
                Side-by-Side 50% / 50% BBox Reviewer
              </span>
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <PdfBboxCanvas
                page={activePage}
                allPages={session.pages}
                pageItems={pageItems}
                selectedItem={selectedItem}
                activeRegionKind={activeRegionKind}
                onChangePage={(pageNo) => {
                  setActivePageNo(pageNo);
                  const firstOnPage = session.items.find((it) => it.pageNo === pageNo);
                  if (firstOnPage) setSelectedItemId(firstOnPage.id);
                }}
                onSelectRegionKind={setActiveRegionKind}
                onSelectItem={handleSelectItem}
                onRecropRegion={handleRecropRegion}
                zoomPercent={zoomPercent}
                onZoomChange={setZoomPercent}
              />

              <QuestionInspectorPanel
                items={session.items}
                selectedItem={selectedItem}
                actionFeedback={actionFeedback}
                isSubmitting={isSubmitting}
                onSelectItem={handleSelectItem}
                onUpdateItem={handleUpdateItem}
                onMarkVerified={handleMarkVerified}
                onSaveStructure={() =>
                  void postStudioAction(
                    `/api/v1/exam-imports/${encodeURIComponent(runId)}/structure`,
                    { items: session.items },
                    `Đã lưu cấu trúc & vùng cắt BBox cho ${selectedItem.stableCode}.`,
                  )
                }
                onApproveItem={(itemId) =>
                  void postStudioAction(
                    `/api/v1/exam-imports/${encodeURIComponent(runId)}/approve`,
                    { item_id: itemId },
                    `Đã duyệt Approve cho câu ${selectedItem.stableCode}.`,
                  )
                }
                onPublishItem={(itemId) =>
                  void postStudioAction(
                    `/api/v1/review-tasks/${encodeURIComponent(itemId)}/decisions`,
                    { decision: "PUBLISH" },
                    `Đã Publish câu ${selectedItem.stableCode} lên Kho chuẩn.`,
                  )
                }
              />
            </div>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
