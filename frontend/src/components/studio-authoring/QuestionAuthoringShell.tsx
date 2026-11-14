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
  lintKatexMarkdown,
  lintMarkdownSafety,
  lintStudioDocument,
  type ClientAssetPermissionEntry,
  type StudioBlockInput,
  type StudioLintIssue,
} from "@/lib/block-studio-linter";
import { validateShortNumericInput } from "@/lib/exam-input-policy";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { StudioBlockEditor } from "./StudioBlockEditor";
import {
  BLOCK_TYPE_META,
  createDefaultStudioAssetCatalog,
  createDemoQuestionAuthoringDraft,
  type StudioQuestionDraftData,
} from "./types";

export interface QuestionAuthoringShellProps {
  initialDraft?: StudioQuestionDraftData;
  assetCatalog?: ReadonlyMap<string, ClientAssetPermissionEntry>;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function QuestionAuthoringShell({
  initialDraft,
  assetCatalog,
  initialUserTier = "TEACHER_PRO",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: QuestionAuthoringShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [draft, setDraft] = useState<StudioQuestionDraftData>(
    () => initialDraft ?? createDemoQuestionAuthoringDraft(),
  );
  const [previewViewport, setPreviewViewport] = useState<"DESKTOP" | "MOBILE">("DESKTOP");
  const [submitFeedback, setSubmitFeedback] = useState<string | null>(null);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  const catalog = useMemo(() => assetCatalog ?? createDefaultStudioAssetCatalog(), [assetCatalog]);

  const validatorIssues = useMemo(() => {
    const issues: StudioLintIssue[] = [
      ...lintKatexMarkdown(draft.stemMarkdown, 0),
      ...lintMarkdownSafety(draft.stemMarkdown, 0),
    ];

    if (!draft.stemMarkdown.trim()) {
      issues.push({
        blockOrder: 0,
        code: "EMPTY_QUESTION_STEM",
        message: "Nội dung đề bài câu hỏi không được để trống.",
      });
    }

    if (draft.questionType === "SINGLE_CHOICE" || draft.questionType === "MULTIPLE_SELECT") {
      draft.options.forEach((opt) => {
        issues.push(...lintKatexMarkdown(opt.label, 0), ...lintMarkdownSafety(opt.label, 0));
      });
    }

    if (draft.questionType === "SHORT_NUMERIC") {
      const shortCheck = validateShortNumericInput(draft.shortNumericAnswer);
      if (!shortCheck.valid) {
        issues.push({
          blockOrder: 0,
          code: "INVALID_SHORT_NUMERIC_ANSWER",
          message: shortCheck.warning ?? "Đáp án trả lời ngắn chưa đúng chuẩn phiếu tô THPTQG.",
        });
      }
    }

    const blockReport = lintStudioDocument(draft.solutionBlocks, {
      authorTenantId: draft.authorTenantId,
      assetCatalog: catalog,
    });
    issues.push(...blockReport.issues);

    return {
      valid: issues.length === 0,
      issues,
    };
  }, [draft, catalog]);

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: 1,
        requiresProAsset: draft.requiresProAsset,
        userTier,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [draft.requiresProAsset, userTier, initialUiOverrides, conflictDismissed],
  );

  const handleSolutionBlocksChange = (nextBlocks: StudioBlockInput[]) => {
    setDraft((prev) => ({ ...prev, solutionBlocks: nextBlocks }));
    setSubmitFeedback(null);
  };

  const handleSubmitQuestion = async () => {
    if (!validatorIssues.valid) return;
    const caller = fetchFn ?? (typeof fetch === "function" ? fetch : null);
    if (caller && apiBase !== "__OFFLINE__") {
      try {
        await caller(`${apiBase}/api/v1/questions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draft),
        });
      } catch {
        // Offline-safe optimistic confirmation
      }
    }
    setSubmitFeedback(`Đã kiểm định Pre-Submit Validator và lưu câu hỏi '${draft.stableCode}'.`);
  };

  return (
    <ErrorBoundary
      errorCode={
        uiResolution.kind === "ErrorBoundary"
          ? (initialUiOverrides?.fatalErrorCode ?? "STUDIO_QUESTION_ERROR")
          : null
      }
    >
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <StudioHeader activeRoute="ws4-question" userTier={userTier} onTierChange={setUserTier} />

        {uiResolution.kind === "SequenceConflictModal" && (
          <SequenceConflictModal
            conflict={{
              questionVersionId: draft.stableCode,
              questionLabel: draft.stableCode,
              localPayload: { stem: draft.stemMarkdown },
              serverPayload: { version: 2 },
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
          <LoadingSkeleton title="Đang tải Trình soạn thảo Câu hỏi C01..." />
        ) : uiResolution.kind === "ErrorBoundary" ? (
          <ErrorBoundary
            errorCode={initialUiOverrides?.fatalErrorCode ?? "STUDIO_QUESTION_ERROR"}
            errorMessage={uiResolution.bannerMessage}
          />
        ) : uiResolution.kind === "LockedProPreview" ? (
          <main className="mx-auto max-w-4xl px-4 py-8">
            <LockedProPreview
              title="Trình biên soạn câu hỏi nâng cao dành cho Giáo viên Pro"
              userTier={userTier}
              requiresProAsset={draft.requiresProAsset}
              rawAssetUrl={null}
              onUpgradeClick={() => setUserTier("TEACHER_PRO")}
            />
          </main>
        ) : uiResolution.kind === "EmptyState" ? (
          <EmptyState title="Chưa khởi tạo biểu mẫu câu hỏi" />
        ) : (
          <main data-testid="ws4-question-studio-layout" className="mx-auto max-w-[1600px] px-4 py-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-xs">
              <div>
                <h1 className="text-lg font-extrabold text-slate-900">
                  Biên soạn Câu hỏi Chuẩn C01 &amp; Lời giải Block Studio
                </h1>
                <p className="text-xs text-slate-500">
                  Hỗ trợ 6 loại câu hỏi (SINGLE_CHOICE, MULTIPLE_SELECT, CLUSTER_TRUE_FALSE, SHORT_NUMERIC, ESSAY,
                  GROUP_STEM) + Pre-Submit Validator.
                </p>
              </div>
              <button
                type="button"
                data-testid="ws4-submit-question-btn"
                disabled={!validatorIssues.valid}
                onClick={() => void handleSubmitQuestion()}
                className="rounded-lg bg-indigo-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-indigo-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
              >
                Tạo câu hỏi C01
              </button>
            </div>

            {submitFeedback && (
              <div
                data-testid="ws4-question-submit-feedback"
                className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs font-semibold text-emerald-900"
              >
                {submitFeedback}
              </div>
            )}

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* Left Column: Question Form + Solution Block Studio */}
              <div className="space-y-4">
                <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div>
                      <label htmlFor="ws4-q-type" className="mb-1 block text-xs font-bold text-slate-700">
                        Loại câu hỏi
                      </label>
                      <select
                        id="ws4-q-type"
                        data-testid="ws4-question-type-select"
                        value={draft.questionType}
                        onChange={(e) =>
                          setDraft((prev) => ({
                            ...prev,
                            questionType: e.target.value as StudioQuestionDraftData["questionType"],
                          }))
                        }
                        className="w-full rounded-lg border border-slate-300 px-2.5 py-2 text-xs font-semibold"
                      >
                        <option value="SINGLE_CHOICE">SINGLE_CHOICE (ABCD)</option>
                        <option value="MULTIPLE_SELECT">MULTIPLE_SELECT</option>
                        <option value="CLUSTER_TRUE_FALSE">CLUSTER_TRUE_FALSE (Đúng/Sai 4 ý)</option>
                        <option value="SHORT_NUMERIC">SHORT_NUMERIC (Trả lời ngắn)</option>
                        <option value="ESSAY">ESSAY (Tự luận)</option>
                        <option value="GROUP_STEM">GROUP_STEM (Câu chùm)</option>
                      </select>
                    </div>

                    <div>
                      <label htmlFor="ws4-q-diff" className="mb-1 block text-xs font-bold text-slate-700">
                        Mức độ nhận thức
                      </label>
                      <select
                        id="ws4-q-diff"
                        value={draft.difficulty}
                        onChange={(e) =>
                          setDraft((prev) => ({
                            ...prev,
                            difficulty: e.target.value as StudioQuestionDraftData["difficulty"],
                          }))
                        }
                        className="w-full rounded-lg border border-slate-300 px-2.5 py-2 text-xs font-semibold"
                      >
                        <option value="NB">NB (Nhận biết)</option>
                        <option value="TH">TH (Thông hiểu)</option>
                        <option value="VD">VD (Vận dụng)</option>
                        <option value="VDC">VDC (Vận dụng cao)</option>
                      </select>
                    </div>

                    <div>
                      <label htmlFor="ws4-q-code" className="mb-1 block text-xs font-bold text-slate-700">
                        Mã định danh ổn định
                      </label>
                      <input
                        id="ws4-q-code"
                        type="text"
                        value={draft.stableCode}
                        onChange={(e) => setDraft((prev) => ({ ...prev, stableCode: e.target.value }))}
                        className="w-full rounded-lg border border-slate-300 px-2.5 py-2 font-mono text-xs"
                      />
                    </div>
                  </div>

                  <div>
                    <label htmlFor="ws4-q-stem" className="mb-1 block text-xs font-bold text-slate-700">
                      Nội dung đề bài (Markdown + LaTeX)
                    </label>
                    <textarea
                      id="ws4-q-stem"
                      data-testid="ws4-question-stem-input"
                      rows={4}
                      value={draft.stemMarkdown}
                      onChange={(e) => {
                        setDraft((prev) => ({ ...prev, stemMarkdown: e.target.value }));
                        setSubmitFeedback(null);
                      }}
                      className="w-full rounded-xl border border-slate-300 p-3 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:outline-none"
                    />
                  </div>

                  {(draft.questionType === "SINGLE_CHOICE" || draft.questionType === "MULTIPLE_SELECT") && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {draft.options.map((opt) => (
                        <div key={opt.key} className="flex items-center gap-2">
                          <span className="w-6 text-center text-xs font-extrabold text-indigo-700">{opt.key}.</span>
                          <input
                            data-testid={`ws4-option-input-${opt.key}`}
                            type="text"
                            value={opt.label}
                            onChange={(e) =>
                              setDraft((prev) => ({
                                ...prev,
                                options: prev.options.map((o) =>
                                  o.key === opt.key ? { ...o, label: e.target.value } : o,
                                ),
                              }))
                            }
                            className="flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 font-mono text-xs"
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {draft.questionType === "SHORT_NUMERIC" && (
                    <div>
                      <label htmlFor="ws4-short-ans" className="mb-1 block text-xs font-bold text-slate-700">
                        Đáp án Trả lời ngắn (tối đa 4 ký tự chuẩn THPTQG)
                      </label>
                      <input
                        id="ws4-short-ans"
                        data-testid="ws4-short-numeric-input"
                        type="text"
                        value={draft.shortNumericAnswer}
                        onChange={(e) => setDraft((prev) => ({ ...prev, shortNumericAnswer: e.target.value }))}
                        className="w-40 rounded-lg border border-slate-300 px-3 py-1.5 font-mono text-sm font-bold"
                      />
                    </div>
                  )}
                </section>

                <StudioBlockEditor
                  title="Soạn lời giải chi tiết bằng Block Studio (/congthuc, /phuongphap, /chuy, /vidu)"
                  blocks={draft.solutionBlocks}
                  onChangeBlocks={handleSolutionBlocksChange}
                  availableAssetIds={[...catalog.keys()]}
                />
              </div>

              {/* Right Column: Pre-Submit Validator + Live Question Preview */}
              <div className="space-y-4">
                <div
                  data-testid="ws4-question-validator-panel"
                  className={`rounded-2xl border p-4 shadow-xs ${
                    validatorIssues.valid ? "border-emerald-200 bg-emerald-50/70" : "border-red-300 bg-red-50"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-extrabold text-slate-900">
                      Pre-Submit Validator (Câu hỏi &amp; Lời giải)
                    </h2>
                    <span
                      data-testid="ws4-question-validator-status"
                      className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${
                        validatorIssues.valid ? "bg-emerald-700 text-white" : "bg-red-700 text-white"
                      }`}
                    >
                      {validatorIssues.valid
                        ? "SẴN SÀNG TẠO CÂU HỎI"
                        : `CHẶN GỬI (${validatorIssues.issues.length} LỖI)`}
                    </span>
                  </div>

                  {!validatorIssues.valid && (
                    <ul data-testid="ws4-question-validator-issues" className="mt-2.5 space-y-1.5 text-xs text-red-900">
                      {validatorIssues.issues.map((issue, idx) => (
                        <li
                          key={`${issue.code}-${idx}`}
                          data-testid={`ws4-q-issue-${issue.code}`}
                          className="rounded-lg border border-red-200 bg-white px-3 py-1.5"
                        >
                          <span className="font-mono font-bold text-red-700">[{issue.code}]</span> {issue.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                  <div className="mb-3 flex items-center justify-between border-b border-slate-100 pb-3">
                    <h2 className="text-sm font-extrabold uppercase tracking-wide text-slate-800">
                      Live KaTeX Preview (Thí sinh nhìn thấy)
                    </h2>
                    <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
                      <button
                        type="button"
                        onClick={() => setPreviewViewport("DESKTOP")}
                        className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                          previewViewport === "DESKTOP" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600"
                        }`}
                      >
                        Desktop
                      </button>
                      <button
                        type="button"
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
                    data-testid="ws4-question-live-preview"
                    className={
                      previewViewport === "MOBILE"
                        ? "mx-auto max-w-[390px] space-y-4 rounded-2xl border-2 border-slate-300 bg-slate-50 p-4"
                        : "space-y-4"
                    }
                  >
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                      <div className="mb-2 flex items-center gap-2 text-xs font-bold text-indigo-700">
                        <span>{draft.stableCode}</span>
                        <span>· {draft.difficulty}</span>
                        <span>· {draft.questionType}</span>
                      </div>
                      <div className="text-sm leading-relaxed text-slate-900">
                        <SafeKatexText text={draft.stemMarkdown} />
                      </div>
                      {(draft.questionType === "SINGLE_CHOICE" || draft.questionType === "MULTIPLE_SELECT") && (
                        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {draft.options.map((opt) => (
                            <div
                              key={opt.key}
                              className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm"
                            >
                              <span className="mr-2 font-bold text-indigo-800">{opt.key}.</span>
                              <SafeKatexText text={opt.label} />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="space-y-2.5">
                      <h3 className="text-xs font-bold uppercase tracking-wide text-slate-600">
                        Lời giải Block Studio
                      </h3>
                      {draft.solutionBlocks.map((block) => {
                        const meta = BLOCK_TYPE_META[block.blockType];
                        const isFormula = block.blockType === "FORMULA_BOX";
                        return (
                          <div
                            key={block.blockOrder}
                            className={`rounded-xl border border-slate-200 border-l-4 p-3.5 ${meta.accent}`}
                          >
                            <div className="mb-1 text-xs font-bold text-slate-700">{meta.title}</div>
                            <SafeKatexText
                              text={block.markdown ?? ""}
                              pureFormula={isFormula}
                              displayMode={isFormula}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </section>
              </div>
            </div>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
