"use client";

import React, { useMemo, useState } from "react";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import {
  computeMatrixShortages,
  validateThptqg2025Preset,
  type MatrixCellSpec,
  type SubjectGroup2025,
} from "@/lib/matrix-builder";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { isUuid, shouldSyncLiveEntity, workspaceCall } from "@/lib/workspace-api";
import { TeacherHeader } from "./TeacherHeader";
import {
  createDemoMatrixBuilderData,
  MATRIX_DIFFICULTIES,
  MATRIX_ITEM_TYPES,
  matrixCellKey,
  type MatrixBuilderSessionData,
} from "./types";

export interface MatrixExamBuilderShellProps {
  initialData?: MatrixBuilderSessionData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function MatrixExamBuilderShell({
  initialData,
  initialUserTier = "TEACHER_PRO",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: MatrixExamBuilderShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [data, setData] = useState<MatrixBuilderSessionData>(
    () => initialData ?? createDemoMatrixBuilderData("MATH"),
  );
  const [desiredMap, setDesiredMap] = useState<Record<string, number>>(
    () => ({ ...(initialData ?? createDemoMatrixBuilderData("MATH")).initialDesired }),
  );
  const [randomSeed, setRandomSeed] = useState<number>(
    () => (initialData ?? createDemoMatrixBuilderData("MATH")).randomSeed,
  );
  const [statusFeedback, setStatusFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  const cells: MatrixCellSpec[] = useMemo(() => {
    const out: MatrixCellSpec[] = [];
    for (const topic of data.topics) {
      for (const difficulty of MATRIX_DIFFICULTIES) {
        for (const itemType of MATRIX_ITEM_TYPES) {
          const key = matrixCellKey(topic.topicId, difficulty, itemType.key);
          out.push({
            topicId: topic.topicId,
            difficulty,
            questionType: itemType.key,
            desiredCount: desiredMap[key] ?? 0,
            availableCount: data.availability[key] ?? 0,
          });
        }
      }
    }
    return out;
  }, [data.topics, data.availability, desiredMap]);

  const shortageReport = useMemo(() => computeMatrixShortages(cells), [cells]);

  const partCounts = useMemo(() => {
    let part1SingleChoice = 0;
    let part2ClusterTrueFalse = 0;
    let part3ShortNumeric = 0;
    for (const cell of cells) {
      if (cell.questionType === "SINGLE_CHOICE") part1SingleChoice += Math.max(0, cell.desiredCount);
      if (cell.questionType === "CLUSTER_TRUE_FALSE")
        part2ClusterTrueFalse += Math.max(0, cell.desiredCount);
      if (cell.questionType === "SHORT_NUMERIC") part3ShortNumeric += Math.max(0, cell.desiredCount);
    }
    return { part1SingleChoice, part2ClusterTrueFalse, part3ShortNumeric };
  }, [cells]);

  const presetValidation = useMemo(
    () => validateThptqg2025Preset(data.subjectGroup, partCounts),
    [data.subjectGroup, partCounts],
  );

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: data.topics.length,
        requiresProAsset: data.requiresProAsset,
        userTier,
        previewPdfUrl: data.previewPdfUrl,
        rawAssetUrl: data.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [data, userTier, initialUiOverrides, conflictDismissed],
  );

  const shortageLookup = useMemo(() => {
    const map = new Map<string, number>();
    for (const sc of shortageReport.shortages) {
      map.set(matrixCellKey(sc.topicId, sc.difficulty, sc.questionType), sc.shortage);
    }
    return map;
  }, [shortageReport.shortages]);

  const handlePresetSwitch = (group: SubjectGroup2025) => {
    const next = createDemoMatrixBuilderData(group);
    setData(next);
    setDesiredMap({ ...next.initialDesired });
    setRandomSeed(next.randomSeed);
    setStatusFeedback(
      group === "MATH"
        ? "Đã nạp khung THPTQG 2025+ Môn Toán (90 phút · 22 câu · 10.0 điểm)."
        : "Đã nạp khung THPTQG 2025+ Khoa học Tự nhiên (50 phút · 28 câu · 10.0 điểm).",
    );
  };

  const handleDesiredChange = (key: string, rawVal: string) => {
    const parsed = rawVal.trim() === "" ? 0 : Number(rawVal);
    setDesiredMap((prev) => ({
      ...prev,
      [key]: Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0,
    }));
  };

  const runApiMutation = async (urlPath: string, body: unknown, fallbackMessage: string) => {
    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    try {
      const result = await workspaceCall({
        url: urlPath,
        method: "POST",
        json: body,
        fetchFn,
        apiBase,
      });
      if (!result.ok) {
        setApiError(result.error);
        setApiErrorStatus(result.status);
        setStatusFeedback(null);
        return;
      }
      setStatusFeedback(fallbackMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAssembleExam = () => {
    if (!shortageReport.readyToAssemble || !presetValidation.valid) return;
    const totalCount =
      partCounts.part1SingleChoice + partCounts.part2ClusterTrueFalse + partCounts.part3ShortNumeric;
    if (!shouldSyncLiveEntity({ fetchFn, apiBase, entityIds: [data.examId] })) {
      setApiError(null);
      setApiErrorStatus(null);
      setStatusFeedback(
        `Đã bốc tự động đề thi mẫu ${totalCount} câu (seed=${randomSeed}) đúng chuẩn THPTQG 2025+ (mô phỏng demo cục bộ).`,
      );
      return;
    }
    const useStrictAssemblePayload = !fetchFn || isUuid(data.examId);
    void runApiMutation(
      `/api/v1/assessments/${encodeURIComponent(data.examId)}/assemble`,
      useStrictAssemblePayload
        ? {
            framework_code: "THPTQG_2025",
            subject_code: data.subjectGroup === "MATH" ? "math" : "physics",
            random_seed: randomSeed,
            taxonomy_node_ids: [],
          }
        : {
            random_seed: randomSeed,
            subject_group: data.subjectGroup,
            cells: cells.filter((c) => c.desiredCount > 0),
          },
      `Đã bốc tự động đề thi ${totalCount} câu (seed=${randomSeed}) đúng chuẩn THPTQG 2025+.`,
    );
  };

  const handleQueueAiGapFill = () => {
    if (shortageReport.shortages.length === 0) return;
    const firstTopicId = shortageReport.shortages[0]?.topicId;
    // Backend POST /api/v1/ai/generations requires CreateGenerationRequest (grounded DB anchors + draft_stem),
    // which the matrix gap-fill widget does not supply directly from browser demo fixtures.
    if (!shouldSyncLiveEntity({ fetchFn, apiBase, entityIds: [firstTopicId] }) || !fetchFn) {
      setApiError(null);
      setApiErrorStatus(null);
      setStatusFeedback(
        `Đã tạo ${shortageReport.totalShortage} đề xuất E03 ở chế độ demo cục bộ (cần nguồn anchor DB thật và taxonomy UUID để đẩy lên hàng đợi duyệt A09).`,
      );
      return;
    }
    void runApiMutation(
      "/api/v1/ai/generations",
      {
        acquisition_method: "AI_GENERATED",
        shortages: shortageReport.shortages,
      },
      `Đã gửi ${shortageReport.totalShortage} yêu cầu E03 sinh câu hỏi biến thể vào hàng đợi duyệt A09 (không vào thẳng PUBLISHED).`,
    );
  };

  const renderMainContent = () => {
    if (uiResolution.kind === "LoadingSkeleton") {
      return <LoadingSkeleton title="Đang tải Matrix Exam Builder..." />;
    }
    if (uiResolution.kind === "ErrorBoundary") {
      return (
        <ErrorBoundary
          errorCode={initialUiOverrides?.fatalErrorCode ?? "MATRIX_LOAD_ERROR"}
          errorMessage={uiResolution.bannerMessage}
        />
      );
    }
    if (uiResolution.kind === "EmptyState") {
      return (
        <EmptyState
          title="Chưa có ma trận chuyên đề SGK"
          description="Vui lòng chọn chương trình SGK Kết nối tri thức để dựng ma trận đề."
        />
      );
    }
    if (uiResolution.kind === "LockedProPreview") {
      return (
        <LockedProPreview
          title="Tính năng Ma trận đề nâng cao dành cho TEACHER_PRO"
          previewPdfUrl={uiResolution.exposedAssetUrl}
          onUpgradeClick={() => setUserTier("TEACHER_PRO")}
        />
      );
    }

    const canAssemble = shortageReport.readyToAssemble && presetValidation.valid && !isSubmitting;

    return (
      <div className="mx-auto max-w-[1600px] space-y-5 px-4 py-5">
        {apiError && (
          <WorkspaceApiAlert
            message={apiError}
            status={apiErrorStatus}
            onDismiss={() => setApiError(null)}
          />
        )}
        {uiResolution.kind === "OfflineRetryBanner" && (
          <OfflineRetryBanner pendingCount={initialUiOverrides?.pendingAutosaveCount ?? 0} />
        )}

        {/* Top Control Bar: Presets & Preset Validation */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider text-blue-700">
                C03 Assessment Blueprint · C04 Deterministic Assembly
              </span>
              <h1 className="mt-1 text-xl font-extrabold text-slate-900">{data.examTitle}</h1>
              <p className="mt-1 text-xs text-slate-600">
                Chỉ chọn câu hỏi đạt <code>PUBLISHED</code> + <code>VERIFIED</code> + bản quyền hợp lệ, chống trùng{" "}
                <code>duplicate_cluster_id</code> và bốc tất định theo <code>random_seed</code>.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                data-testid="preset-math-btn"
                onClick={() => handlePresetSwitch("MATH")}
                className={`rounded-lg px-3.5 py-2 text-xs font-bold transition ${
                  data.subjectGroup === "MATH"
                    ? "bg-blue-700 text-white"
                    : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                Toán THPTQG 2025+ (22 câu · 90p)
              </button>
              <button
                type="button"
                data-testid="preset-science-btn"
                onClick={() => handlePresetSwitch("NATURAL_SCIENCE")}
                className={`rounded-lg px-3.5 py-2 text-xs font-bold transition ${
                  data.subjectGroup === "NATURAL_SCIENCE"
                    ? "bg-blue-700 text-white"
                    : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                Lý / Hóa / Sinh 2025+ (28 câu · 50p)
              </button>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-[11px] font-semibold text-slate-500">Phần I · ABCD (0.25đ/câu)</div>
              <div className="mt-1 text-lg font-extrabold text-slate-900" data-testid="part1-count">
                {partCounts.part1SingleChoice} câu
              </div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-[11px] font-semibold text-slate-500">Phần II · Đúng/Sai 4 ý (1.0đ/câu)</div>
              <div className="mt-1 text-lg font-extrabold text-slate-900" data-testid="part2-count">
                {partCounts.part2ClusterTrueFalse} câu
              </div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-[11px] font-semibold text-slate-500">
                Phần III · Trả lời ngắn ({data.subjectGroup === "MATH" ? "0.5đ" : "0.25đ"}/câu)
              </div>
              <div className="mt-1 text-lg font-extrabold text-slate-900" data-testid="part3-count">
                {partCounts.part3ShortNumeric} câu
              </div>
            </div>
            <div
              data-testid="preset-validation-card"
              className={`rounded-xl border p-3 ${
                presetValidation.valid
                  ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                  : "border-amber-300 bg-amber-50 text-amber-950"
              }`}
            >
              <div className="text-[11px] font-semibold">Tổng điểm / Tổng câu</div>
              <div className="mt-1 text-lg font-extrabold">
                {presetValidation.computedTotalPoints.toFixed(2)} điểm · {presetValidation.actualTotalItems}/
                {presetValidation.expectedTotalItems} câu
              </div>
              {!presetValidation.valid && (
                <ul className="mt-1 list-disc pl-4 text-[11px]">
                  {presetValidation.errors.map((err) => (
                    <li key={err}>{err}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>

        {/* ShortageReport Alert Banner */}
        {!shortageReport.readyToAssemble ? (
          <section
            data-testid="shortage-report-panel"
            role="alert"
            className="rounded-2xl border border-rose-300 bg-rose-50 p-4 text-rose-950 shadow-xs"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-extrabold uppercase tracking-wide text-rose-800">
                  ShortageReport: Thiếu {shortageReport.totalShortage} câu trong kho đề đạt chuẩn
                </h2>
                <p className="mt-0.5 text-xs text-rose-800">
                  Các ô bôi đỏ đang yêu cầu nhiều hơn số câu <code>PUBLISHED + VERIFIED</code> hiện có. Vui lòng điều
                  chỉnh lại ma trận hoặc kích hoạt AI Matrix Gap Filler (E03 -&gt; A09).
                </p>
              </div>
              <button
                type="button"
                data-testid="trigger-ai-gap-fill-btn"
                disabled={shortageReport.shortages.length === 0 || isSubmitting}
                onClick={handleQueueAiGapFill}
                className="rounded-lg bg-rose-700 px-3.5 py-2 text-xs font-bold text-white transition hover:bg-rose-800 disabled:opacity-50"
              >
                Sinh biến thể bù ô thiếu (E03 -&gt; A09)
              </button>
            </div>
            <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {shortageReport.shortages.map((sc) => (
                <li
                  key={matrixCellKey(sc.topicId, sc.difficulty, sc.questionType)}
                  className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-xs font-semibold text-rose-900"
                >
                  {sc.topicId} · {sc.difficulty} · {sc.questionType}: yêu cầu {sc.desiredCount}, khả dụng{" "}
                  {sc.availableCount} (Thiếu {sc.shortage})
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <section
            data-testid="shortage-clear-banner"
            className="rounded-2xl border border-emerald-300 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-900"
          >
            Kho câu hỏi đáp ứng 100% ma trận ({shortageReport.totalDesired} câu). Sẵn sàng bốc đề tự động C04!
          </section>
        )}

        {/* 2D Matrix Table */}
        <section className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-xs">
          <table className="w-full min-w-[1050px] border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-100 text-slate-700">
                <th rowSpan={2} className="w-60 border-r border-slate-200 px-4 py-3 font-extrabold">
                  Chủ đề / Chuyên đề SGK
                </th>
                {MATRIX_DIFFICULTIES.map((diff) => (
                  <th
                    key={diff}
                    colSpan={3}
                    className="border-r border-slate-200 px-2 py-2 text-center font-extrabold"
                  >
                    Mức độ {diff}
                  </th>
                ))}
              </tr>
              <tr className="border-b border-slate-200 bg-slate-50 text-[11px] font-semibold text-slate-600">
                {MATRIX_DIFFICULTIES.map((diff) =>
                  MATRIX_ITEM_TYPES.map((it) => (
                    <th key={`${diff}-${it.key}`} className="border-r border-slate-200 px-2 py-2 text-center">
                      {it.shortLabel}
                    </th>
                  )),
                )}
              </tr>
            </thead>
            <tbody>
              {data.topics.map((topic) => (
                <tr key={topic.topicId} className="border-b border-slate-200 last:border-b-0">
                  <td className="border-r border-slate-200 px-4 py-3 align-middle">
                    <div className="text-[11px] font-bold text-blue-700">{topic.chapterCode}</div>
                    <div className="font-semibold text-slate-900">{topic.topicName}</div>
                  </td>
                  {MATRIX_DIFFICULTIES.map((diff) =>
                    MATRIX_ITEM_TYPES.map((it) => {
                      const key = matrixCellKey(topic.topicId, diff, it.key);
                      const desired = desiredMap[key] ?? 0;
                      const available = data.availability[key] ?? 0;
                      const shortage = shortageLookup.get(key) ?? 0;
                      const isShort = shortage > 0;
                      return (
                        <td
                          key={key}
                          data-testid={`matrix-cell-${topic.topicId}-${diff}-${it.key}`}
                          data-shortage={isShort ? "true" : "false"}
                          className={`border-r border-slate-200 p-1.5 text-center align-middle ${
                            isShort ? "bg-rose-50" : desired > 0 ? "bg-blue-50/40" : "bg-white"
                          }`}
                        >
                          <input
                            type="number"
                            min={0}
                            aria-label={`${topic.topicName} ${diff} ${it.shortLabel}`}
                            data-testid={`matrix-input-${topic.topicId}-${diff}-${it.key}`}
                            value={desired}
                            onChange={(e) => handleDesiredChange(key, e.target.value)}
                            className={`w-14 rounded border px-1.5 py-1 text-center text-xs font-bold outline-none ${
                              isShort
                                ? "border-rose-500 bg-white text-rose-900 focus:ring-2 focus:ring-rose-300"
                                : "border-slate-300 bg-white text-slate-900 focus:border-blue-600"
                            }`}
                          />
                          <div
                            className={`mt-1 text-[10px] font-semibold ${
                              isShort ? "text-rose-700" : "text-slate-500"
                            }`}
                          >
                            {isShort ? `Thiếu ${shortage} (kho ${available})` : `Kho: ${available}`}
                          </div>
                        </td>
                      );
                    }),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {/* Bottom Assembly Action Bar */}
        <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs font-semibold text-slate-700">
              <span>random_seed:</span>
              <input
                type="number"
                data-testid="random-seed-input"
                value={randomSeed}
                onChange={(e) => setRandomSeed(Number(e.target.value) || 0)}
                className="w-28 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-bold text-slate-900"
              />
            </label>
            {statusFeedback && (
              <span
                role="status"
                data-testid="matrix-builder-feedback"
                className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800"
              >
                {statusFeedback}
              </span>
            )}
          </div>

          <button
            type="button"
            data-testid="assemble-exam-btn"
            disabled={!canAssemble}
            onClick={handleAssembleExam}
            className="rounded-xl bg-blue-700 px-5 py-2.5 text-sm font-bold text-white shadow-xs transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
          >
            Bốc đề tự động C04 ({shortageReport.totalDesired} câu)
          </button>
        </section>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <TeacherHeader activeRoute="exam-builder" userTier={userTier} onTierChange={setUserTier} />
      <main>{renderMainContent()}</main>
      {uiResolution.kind === "SequenceConflictModal" && (
        <SequenceConflictModal
          conflict={{
            questionVersionId: "matrix-blueprint-sync",
            questionLabel: "Ma trận Đề THPTQG 2025+",
            localPayload: "Cấu hình ô ma trận đang sửa",
            serverPayload: "Ma trận đã cập nhật trên máy chủ",
          }}
          onResolve={() => setConflictDismissed(true)}
        />
      )}
    </div>
  );
}
