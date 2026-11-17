"use client";

import React, { useEffect, useMemo, useState } from "react";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { shouldHydrateLive, workspaceCall } from "@/lib/workspace-api";
import { AiTutorErrorDrawer } from "./AiTutorErrorDrawer";
import { CurriculumTreeSidebar } from "./CurriculumTreeSidebar";
import { H2EduHeader } from "./H2EduHeader";
import { LessonPracticeColumn } from "./LessonPracticeColumn";
import {
  createDemoStudyDocument,
  type CurriculumTreeNode,
  type StudyDocumentPayload,
  type StudyHubErrorNoteItem,
  type StudyPracticeItem,
} from "./types";

export interface StudyHubShellProps {
  slug: string;
  initialDocument?: StudyDocumentPayload;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  activeExamAttemptId?: string | null;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function StudyHubShell({
  slug,
  initialDocument,
  initialUserTier = "FREE",
  initialUiOverrides,
  activeExamAttemptId = null,
  fetchFn,
  apiBase = "",
}: StudyHubShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [doc, setDoc] = useState<StudyDocumentPayload>(() => initialDocument ?? createDemoStudyDocument(slug));
  const [activeNodeId, setActiveNodeId] = useState<string>(doc.activeNodeId);
  const [errorNotes, setErrorNotes] = useState<readonly StudyHubErrorNoteItem[]>(doc.errorNotes);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  useEffect(() => {
    if (!shouldHydrateLive({ hasInitialData: Boolean(initialDocument), apiBase })) return;
    let active = true;
    void Promise.all([
      workspaceCall<{
        items?: Array<{ id?: string; title?: string; slug?: string; subject_code?: string }>;
      }>({
        url: `/api/v1/search?q=${encodeURIComponent(slug)}&limit=5`,
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      }),
      workspaceCall<
        Array<{
          id?: string;
          question_version_id?: string;
          consecutive_correct_count?: number;
          status?: string;
          wrong_count?: number;
        }>
      >({
        url: "/api/v1/error-notes",
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      }),
      workspaceCall<{ active_streak?: number }>({
        url: "/api/v1/learners/me/dashboard",
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      }),
    ]).then(([searchRes, notesRes, dashRes]) => {
      if (!active) return;
      const errRes = [searchRes, notesRes, dashRes].find((r) => !r.ok);
      if (errRes && !errRes.ok && errRes.status !== 404) {
        setApiError(errRes.error);
        setApiErrorStatus(errRes.status);
      }
      if (searchRes.ok && searchRes.data?.items && searchRes.data.items.length > 0) {
        const hit = searchRes.data.items[0]!;
        if (hit.title) {
          setDoc((prev) => ({ ...prev, title: hit.title! }));
        }
      }
      if (notesRes.ok && Array.isArray(notesRes.data) && notesRes.data.length > 0) {
        setErrorNotes(
          notesRes.data.map((n, idx) => ({
            id: String(n.id ?? `en-${idx + 1}`),
            questionCode: String(n.question_version_id ?? `Q-${idx + 1}`).slice(0, 12),
            topicTitle: "Sổ tay câu sai thực tế (D05)",
            summaryLatex: `Số lần làm sai: ${n.wrong_count ?? 1}`,
            diagnosis: "Đồng bộ từ máy chủ /api/v1/error-notes — cần đúng liên tiếp 2 câu biến thể.",
            consecutiveCorrectCount: n.consecutive_correct_count ?? 0,
            status: n.status === "RESOLVED" ? "RESOLVED" : "ACTIVE",
            variantDrillPrompt: "Ôn tập biến thể tương tự trong Sổ tay câu sai",
            variantCorrectOptionKey: "A",
            variantOptions: [
              { key: "A", label: "Đáp án chuẩn" },
              { key: "B", label: "Phương án nhiễu" },
            ],
          })),
        );
      }
    });
    return () => {
      active = false;
    };
  }, [apiBase, fetchFn, initialDocument, slug]);

  const uiResolution = useMemo(() => {
    const itemCount =
      doc.blocks.length === 0 && doc.practiceItems.length === 0 ? 0 : doc.blocks.length + doc.practiceItems.length;
    return resolveWorkspaceUiState({
      isLoading: false,
      itemCount,
      requiresProAsset: doc.requiresProAsset,
      userTier,
      previewPdfUrl: doc.previewPdfUrl,
      // Security: never expose rawAssetUrl while locked for FREE accounts.
      rawAssetUrl: doc.rawAssetUrl,
      ...initialUiOverrides,
      ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
    });
  }, [doc, userTier, initialUiOverrides, conflictDismissed]);

  const handleSelectNode = (node: CurriculumTreeNode) => {
    setActiveNodeId(node.id);
    if (node.slug !== doc.slug) {
      const next = createDemoStudyDocument(node.slug);
      setDoc(next);
      setErrorNotes(next.errorNotes);
    }
  };

  const handleRecordIncorrect = (item: StudyPracticeItem) => {
    setErrorNotes((prev) => {
      const existingIdx = prev.findIndex((n) => n.id === `prac-err-${item.id}`);
      if (existingIdx >= 0) {
        return prev.map((n, idx) =>
          idx === existingIdx ? { ...n, consecutiveCorrectCount: 0, status: "ACTIVE" } : n,
        );
      }
      const nextNote: StudyHubErrorNoteItem = {
        id: `prac-err-${item.id}`,
        questionCode: item.id.toUpperCase(),
        topicTitle: doc.title,
        summaryLatex: item.promptLatex,
        diagnosis: "Làm sai trong lúc luyện tập tương tác — cần làm đúng liên tiếp 2 câu biến thể.",
        consecutiveCorrectCount: 0,
        status: "ACTIVE",
        variantDrillPrompt: item.promptLatex,
        variantCorrectOptionKey: "A",
        variantOptions: [
          { key: "A", label: "Đáp án chuẩn" },
          { key: "B", label: "Phương án nhiễu" },
        ],
      };
      return [nextNote, ...prev];
    });
  };

  return (
    <ErrorBoundary errorCode={uiResolution.kind === "ErrorBoundary" ? (initialUiOverrides?.fatalErrorCode ?? "LOAD_ERROR") : null}>
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <H2EduHeader
          activeRoute="hoc-lieu"
          userTier={userTier}
          onTierChange={setUserTier}
        />

        {uiResolution.kind === "SequenceConflictModal" && (
          <SequenceConflictModal
            conflict={{
              questionVersionId: slug,
              questionLabel: doc.title,
              localPayload: { progress: "local" },
              serverPayload: { progress: "server" },
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

        {apiError && (
          <div className="mx-auto max-w-[1440px] px-4 pt-4">
            <WorkspaceApiAlert
              message={apiError}
              status={apiErrorStatus}
              onDismiss={() => setApiError(null)}
            />
          </div>
        )}

        {uiResolution.kind === "LoadingSkeleton" ? (
          <LoadingSkeleton title="Đang tải học liệu H2Edu..." />
        ) : uiResolution.kind === "ErrorBoundary" ? (
          <ErrorBoundary
            errorCode={initialUiOverrides?.fatalErrorCode ?? "STUDIO_LOAD_ERROR"}
            errorMessage={uiResolution.bannerMessage}
          />
        ) : uiResolution.kind === "LockedProPreview" ? (
          <main data-testid="workspace1-locked-pro-container" className="mx-auto max-w-4xl px-4 py-8">
            <LockedProPreview
              title={`Tài liệu PRO: ${doc.title}`}
              userTier={userTier}
              requiresProAsset={doc.requiresProAsset}
              previewPdfUrl={uiResolution.exposedAssetUrl}
              rawAssetUrl={null}
              onUpgradeClick={() => setUserTier("STUDENT_PRO")}
            />
            <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              <h2 className="text-sm font-bold text-slate-900">
                Xem trước tối đa 2 trang đầu (PREVIEW_PDF)
              </h2>
              <p className="mt-1 text-xs text-slate-600">
                Tài khoản FREE chỉ được nạp đường dẫn xem trước bảo mật. Đường dẫn tài liệu gốc không được truyền xuống DOM.
              </p>
              {uiResolution.exposedAssetUrl ? (
                <div
                  data-testid="pro-preview-pdf-box"
                  className="mt-3 rounded-xl border border-dashed border-amber-300 bg-amber-50/60 p-4 text-xs text-amber-950"
                >
                  Đang mở khóa bản xem trước 2 trang: <code className="font-mono">{uiResolution.exposedAssetUrl}</code>
                </div>
              ) : (
                <p className="mt-2 text-xs text-slate-500">Chưa có bản PDF xem trước cho mục này.</p>
              )}
            </div>
          </main>
        ) : uiResolution.kind === "EmptyState" ? (
          <EmptyState
            title="Chưa có học liệu trong mục này"
            description="Chương hoặc bài học bạn chọn hiện chưa có khối lý thuyết và bài tập nào được xuất bản."
            actionLabel="Quay lại Công thức Bayes"
            onAction={() => {
              const fallback = createDemoStudyDocument("cong-thuc-bayes");
              setDoc(fallback);
              setErrorNotes(fallback.errorNotes);
            }}
          />
        ) : (
          <main
            data-testid="workspace1-three-column-layout"
            className="mx-auto flex max-w-[1440px] flex-col gap-5 px-4 py-6 lg:flex-row lg:items-start"
          >
            <CurriculumTreeSidebar
              tree={doc.tree}
              activeNodeId={activeNodeId}
              onSelectNode={handleSelectNode}
            />

            <LessonPracticeColumn
              title={doc.title}
              subjectLabel={doc.subjectLabel}
              gradeLabel={doc.gradeLabel}
              bookSeries={doc.bookSeries}
              blocks={doc.blocks}
              practiceItems={doc.practiceItems}
              onRecordIncorrect={handleRecordIncorrect}
            />

            <AiTutorErrorDrawer
              errorNotes={errorNotes}
              activeExamAttemptId={activeExamAttemptId}
              fetchFn={fetchFn}
              apiBase={apiBase}
            />
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
