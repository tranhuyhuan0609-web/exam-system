"use client";

import React, { useMemo, useState } from "react";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
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
}

export function StudyHubShell({
  slug,
  initialDocument,
  initialUserTier = "FREE",
  initialUiOverrides,
  activeExamAttemptId = null,
}: StudyHubShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [doc, setDoc] = useState<StudyDocumentPayload>(() => initialDocument ?? createDemoStudyDocument(slug));
  const [activeNodeId, setActiveNodeId] = useState<string>(doc.activeNodeId);
  const [errorNotes, setErrorNotes] = useState<readonly StudyHubErrorNoteItem[]>(doc.errorNotes);
  const [conflictDismissed, setConflictDismissed] = useState(false);

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
            />
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
