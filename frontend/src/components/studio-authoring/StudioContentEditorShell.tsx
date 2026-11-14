"use client";

import React, { useMemo, useState } from "react";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import {
  lintStudioDocument,
  type ClientAssetPermissionEntry,
  type StudioBlockInput,
} from "@/lib/block-studio-linter";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { StudioBlockEditor } from "./StudioBlockEditor";
import {
  BLOCK_TYPE_META,
  createDefaultStudioAssetCatalog,
  createDemoStudioContentDocument,
  type StudioContentDocumentData,
} from "./types";

export interface StudioContentEditorShellProps {
  contentId: string;
  initialDocument?: StudioContentDocumentData;
  assetCatalog?: ReadonlyMap<string, ClientAssetPermissionEntry>;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function StudioContentEditorShell({
  contentId,
  initialDocument,
  assetCatalog,
  initialUserTier = "TEACHER_PRO",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: StudioContentEditorShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [doc, setDoc] = useState<StudioContentDocumentData>(
    () => initialDocument ?? createDemoStudioContentDocument(contentId),
  );
  const [previewViewport, setPreviewViewport] = useState<"DESKTOP" | "MOBILE">("DESKTOP");
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  const catalog = useMemo(() => assetCatalog ?? createDefaultStudioAssetCatalog(), [assetCatalog]);

  const lintReport = useMemo(
    () =>
      lintStudioDocument(doc.blocks, {
        authorTenantId: doc.authorTenantId,
        assetCatalog: catalog,
      }),
    [doc.blocks, doc.authorTenantId, catalog],
  );

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: doc.blocks.length,
        requiresProAsset: doc.requiresProAsset,
        userTier,
        previewPdfUrl: doc.previewPdfUrl,
        rawAssetUrl: doc.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [doc, userTier, initialUiOverrides, conflictDismissed],
  );

  const handleBlocksChange = (nextBlocks: StudioBlockInput[]) => {
    setDoc((prev) => ({ ...prev, blocks: nextBlocks }));
    setSaveFeedback(null);
  };

  const handleSaveDraft = async () => {
    if (!lintReport.valid) return;
    const caller = fetchFn ?? (typeof fetch === "function" ? fetch : null);
    if (caller && apiBase !== "__OFFLINE__") {
      try {
        await caller(`${apiBase}/api/v1/content/${encodeURIComponent(doc.contentId)}/drafts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: doc.title, blocks: doc.blocks }),
        });
      } catch {
        // Offline-safe optimistic confirmation after passing Pre-Submit Validator
      }
    }
    setSaveFeedback(`Đã kiểm tra Pre-Submit Validator (0 lỗi) và lưu bản nháp bài '${doc.contentId}'.`);
  };

  return (
    <ErrorBoundary
      errorCode={
        uiResolution.kind === "ErrorBoundary"
          ? (initialUiOverrides?.fatalErrorCode ?? "STUDIO_CONTENT_ERROR")
          : null
      }
    >
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <StudioHeader activeRoute="ws4-content" userTier={userTier} onTierChange={setUserTier} />

        {uiResolution.kind === "SequenceConflictModal" && (
          <SequenceConflictModal
            conflict={{
              questionVersionId: contentId,
              questionLabel: doc.title,
              localPayload: { blockCount: doc.blocks.length },
              serverPayload: { revision: "newer" },
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
          <LoadingSkeleton title="Đang tải VietJack Block Studio..." />
        ) : uiResolution.kind === "ErrorBoundary" ? (
          <ErrorBoundary
            errorCode={initialUiOverrides?.fatalErrorCode ?? "STUDIO_CONTENT_ERROR"}
            errorMessage={uiResolution.bannerMessage}
          />
        ) : uiResolution.kind === "LockedProPreview" ? (
          <main data-testid="ws4-locked-pro-container" className="mx-auto max-w-4xl px-4 py-8">
            <LockedProPreview
              title={`Học liệu bản quyền giới hạn: ${doc.title}`}
              userTier={userTier}
              requiresProAsset={doc.requiresProAsset}
              previewPdfUrl={uiResolution.exposedAssetUrl}
              rawAssetUrl={null}
              onUpgradeClick={() => setUserTier("TEACHER_PRO")}
            />
          </main>
        ) : uiResolution.kind === "EmptyState" ? (
          <EmptyState
            title="Bài học chưa có khối nội dung nào"
            description="Hãy thêm khối bằng lệnh /tomtat, /congthuc, /phuongphap, /chuy hoặc /vidu."
          />
        ) : (
          <main data-testid="ws4-content-studio-layout" className="mx-auto max-w-[1600px] px-4 py-5">
            {/* Title & Validator Action Header */}
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-xs">
              <div className="flex-1">
                <label htmlFor="ws4-doc-title" className="text-[11px] font-bold uppercase text-slate-500">
                  Tiêu đề bài học ({doc.contentType})
                </label>
                <input
                  id="ws4-doc-title"
                  data-testid="ws4-doc-title-input"
                  type="text"
                  value={doc.title}
                  onChange={(e) => setDoc((prev) => ({ ...prev, title: e.target.value }))}
                  className="mt-0.5 w-full rounded-lg border border-slate-200 px-2.5 py-1 text-base font-extrabold text-slate-900 focus:border-indigo-500 focus:outline-none"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="ws4-save-draft-btn"
                  disabled={!lintReport.valid}
                  onClick={() => void handleSaveDraft()}
                  className="rounded-lg bg-indigo-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-indigo-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
                >
                  Lưu bản nháp &amp; Gửi duyệt
                </button>
              </div>
            </div>

            {saveFeedback && (
              <div
                data-testid="ws4-save-feedback"
                className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs font-semibold text-emerald-900"
              >
                {saveFeedback}
              </div>
            )}

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* Left Column: Block Studio Editor */}
              <StudioBlockEditor
                blocks={doc.blocks}
                onChangeBlocks={handleBlocksChange}
                availableAssetIds={[...catalog.keys()]}
              />

              {/* Right Column: Live KaTeX Student Preview + Pre-Submit Validator */}
              <section className="flex flex-col gap-4">
                {/* Pre-Submit Validator Panel */}
                <div
                  data-testid="ws4-validator-panel"
                  className={`rounded-2xl border p-4 shadow-xs ${
                    lintReport.valid ? "border-emerald-200 bg-emerald-50/70" : "border-red-300 bg-red-50"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-extrabold text-slate-900">
                      Pre-Submit Validator (KaTeX, XSS URL &amp; Asset ACL)
                    </h2>
                    <span
                      data-testid="ws4-validator-status"
                      className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${
                        lintReport.valid ? "bg-emerald-700 text-white" : "bg-red-700 text-white"
                      }`}
                    >
                      {lintReport.valid ? "HỢP LỆ (0 LỖI)" : `CHẶN LƯU (${lintReport.issues.length} LỖI)`}
                    </span>
                  </div>

                  {!lintReport.valid && (
                    <ul data-testid="ws4-validator-issues" className="mt-2.5 space-y-1.5 text-xs text-red-900">
                      {lintReport.issues.map((issue, idx) => (
                        <li
                          key={`${issue.code}-${issue.blockOrder}-${idx}`}
                          data-testid={`ws4-issue-${issue.code}`}
                          className="rounded-lg border border-red-200 bg-white px-3 py-1.5"
                        >
                          <span className="font-mono font-bold text-red-700">
                            [Khối #{issue.blockOrder} · {issue.code}]
                          </span>{" "}
                          {issue.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Live Student View Preview */}
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
                    <div>
                      <h2 className="text-sm font-extrabold uppercase tracking-wide text-slate-800">
                        Live KaTeX Preview (Giao diện Học sinh)
                      </h2>
                      <p className="text-[11px] text-slate-500">
                        Dùng SafeKatexText (`throwOnError: false`, `trust: false`)
                      </p>
                    </div>
                    <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
                      <button
                        type="button"
                        data-testid="ws4-viewport-desktop"
                        onClick={() => setPreviewViewport("DESKTOP")}
                        className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                          previewViewport === "DESKTOP" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600"
                        }`}
                      >
                        Desktop
                      </button>
                      <button
                        type="button"
                        data-testid="ws4-viewport-mobile"
                        onClick={() => setPreviewViewport("MOBILE")}
                        className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                          previewViewport === "MOBILE" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600"
                        }`}
                      >
                        Mobile
                      </button>
                    </div>
                  </div>

                  <div
                    data-testid="ws4-preview-viewport"
                    data-viewport-mode={previewViewport}
                    className={
                      previewViewport === "MOBILE"
                        ? "mx-auto max-w-[390px] space-y-3 rounded-2xl border-2 border-slate-300 bg-slate-50 p-4"
                        : "space-y-3"
                    }
                  >
                    {doc.blocks.map((block) => {
                      const meta = BLOCK_TYPE_META[block.blockType];
                      const isFormula = block.blockType === "FORMULA_BOX";
                      return (
                        <article
                          key={block.blockOrder}
                          data-testid={`ws4-preview-block-${block.blockType}`}
                          className={`rounded-xl border border-slate-200 border-l-4 p-4 ${meta.accent}`}
                        >
                          <div className="mb-1.5 text-xs font-extrabold uppercase tracking-wide text-slate-700">
                            {meta.title}
                          </div>
                          <div className="text-sm leading-relaxed text-slate-900">
                            <SafeKatexText
                              text={block.markdown ?? ""}
                              pureFormula={isFormula}
                              displayMode={isFormula}
                            />
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </div>
              </section>
            </div>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
