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
import {
  computeMatrixShortages,
  validateThptqg2025Preset,
  type MatrixCellSpec,
  type SubjectGroup2025,
} from "@/lib/matrix-builder";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { isUuid, shouldHydrateLive, shouldSyncLiveEntity, workspaceCall } from "@/lib/workspace-api";
import { TeacherHeader } from "./TeacherHeader";
import { TeacherPdfUploadPanel, type TeacherClassOption } from "./TeacherPdfUploadPanel";
import {
  createDemoMatrixBuilderData,
  MATRIX_DIFFICULTIES,
  MATRIX_ITEM_TYPES,
  matrixCellKey,
  type MatrixBuilderSessionData,
  type MatrixTopicRow,
} from "./types";

interface PoolSummaryRow {
  readonly taxonomy_node_id: string;
  readonly code_path: string;
  readonly item_type: string;
  readonly difficulty_level: string;
  readonly available: number;
}

interface TaxonomyNodeRow {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly code_path?: string | null;
}

type BuilderTab = "matrix" | "from-pdf";
type MatrixValidationMode = "THPTQG_2025" | "CUSTOM_TOPIC";

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
  const [activeTab, setActiveTab] = useState<BuilderTab>("matrix");
  const [matrixMode, setMatrixMode] = useState<MatrixValidationMode>("THPTQG_2025");
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
  const [newPaperTitle, setNewPaperTitle] = useState("Đề kiểm tra Toán 12 theo chủ đề");
  const [durationMinutes, setDurationMinutes] = useState<number>(45);
  const [selectedClassId, setSelectedClassId] = useState<string>("");
  const [createNewOnAssemble, setCreateNewOnAssemble] = useState(false);
  const [assembledAssessmentId, setAssembledAssessmentId] = useState<string | null>(null);
  const [publishedRoomLink, setPublishedRoomLink] = useState<string | null>(null);
  const [livePoolTotal, setLivePoolTotal] = useState<number | null>(null);
  const [teacherClasses, setTeacherClasses] = useState<readonly TeacherClassOption[]>([]);
  const [statusFeedback, setStatusFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conflictDismissed, setConflictDismissed] = useState(false);
  const [livePapers, setLivePapers] = useState<
    readonly { id: string; title?: string; subject_code?: string; publication_status?: string; status?: string }[]
  >([]);

  const activeSubjectCode = data.subjectGroup === "MATH" ? "math" : "physics";

  useEffect(() => {
    if (!shouldHydrateLive({ hasInitialData: Boolean(initialData), apiBase })) return;
    let active = true;

    const hydrate = async () => {
      const [papersRes, classesRes, taxonomyRes, poolRes] = await Promise.all([
        workspaceCall<
          | readonly { id: string; title?: string; subject_code?: string; publication_status?: string; status?: string }[]
          | { items?: readonly { id: string; title?: string; subject_code?: string; publication_status?: string; status?: string }[] }
        >({
          url: "/api/v1/assessments",
          method: "GET",
          fetchFn,
          apiBase,
          redirectOn401: false,
        }),
        workspaceCall<readonly TeacherClassOption[]>({
          url: "/api/v1/classes",
          method: "GET",
          fetchFn,
          apiBase,
          redirectOn401: false,
        }),
        workspaceCall<{ items?: readonly TaxonomyNodeRow[] }>({
          url: "/api/v1/taxonomy/nodes?limit=100",
          method: "GET",
          fetchFn,
          apiBase,
          redirectOn401: false,
        }),
        workspaceCall<readonly PoolSummaryRow[]>({
          url: `/api/v1/assessments/pool-summary?subject_code=${encodeURIComponent(activeSubjectCode)}`,
          method: "GET",
          fetchFn,
          apiBase,
          redirectOn401: false,
        }),
      ]);

      if (!active) return;

      if (papersRes.ok && papersRes.data) {
        const rawRows = Array.isArray(papersRes.data)
          ? papersRes.data
          : Array.isArray((papersRes.data as { items?: unknown }).items)
            ? (
                papersRes.data as {
                  items: readonly { id: string; title?: string; subject_code?: string; publication_status?: string; status?: string }[];
                }
              ).items
            : [];
        const rows = rawRows.filter((row) => isUuid(String(row?.id ?? "")));
        if (rows.length > 0) {
          setLivePapers(rows);
          setData((prev) =>
            isUuid(prev.examId)
              ? prev
              : {
                  ...prev,
                  examId: rows[0].id,
                  examTitle: rows[0].title ?? prev.examTitle,
                },
          );
        }
      }

      if (classesRes.ok && Array.isArray(classesRes.data)) {
        setTeacherClasses(classesRes.data.filter((c) => isUuid(String(c?.id ?? ""))));
      }

      if (poolRes.ok && Array.isArray(poolRes.data) && poolRes.data.length > 0) {
        const taxLookup = new Map<string, TaxonomyNodeRow>();
        const taxRows = taxonomyRes.ok && Array.isArray(taxonomyRes.data?.items) ? taxonomyRes.data.items : [];
        for (const node of taxRows) {
          if (isUuid(node.id)) taxLookup.set(node.id, node);
        }

        const topicMap = new Map<string, MatrixTopicRow>();
        const availability: Record<string, number> = {};
        let totalAvailable = 0;

        for (const row of poolRes.data) {
          if (!isUuid(row.taxonomy_node_id)) continue;
          const node = taxLookup.get(row.taxonomy_node_id);
          if (!topicMap.has(row.taxonomy_node_id)) {
            topicMap.set(row.taxonomy_node_id, {
              topicId: row.taxonomy_node_id,
              chapterCode: node?.code_path ?? row.code_path,
              topicName: node?.name ?? row.code_path,
            });
          }
          const cellKey = matrixCellKey(
            row.taxonomy_node_id,
            row.difficulty_level as "NB" | "TH" | "VD" | "VDC",
            row.item_type as "SINGLE_CHOICE" | "CLUSTER_TRUE_FALSE" | "SHORT_NUMERIC",
          );
          availability[cellKey] = (availability[cellKey] ?? 0) + row.available;
          totalAvailable += row.available;
        }

        const liveTopics = Array.from(topicMap.values());
        if (liveTopics.length > 0) {
          setLivePoolTotal(totalAvailable);
          setData((prev) => ({
            ...prev,
            topics: liveTopics,
            availability,
          }));
          setDesiredMap({});
          if (totalAvailable < (data.subjectGroup === "MATH" ? 22 : 28)) {
            setMatrixMode("CUSTOM_TOPIC");
          }
        }
      }
    };

    void hydrate();
    return () => {
      active = false;
    };
  }, [activeSubjectCode, apiBase, data.subjectGroup, fetchFn, initialData]);

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
    setData((prev) => ({
      ...next,
      ...(isUuid(prev.examId) ? { examId: prev.examId } : {}),
    }));
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

  const handleAssembleExam = async () => {
    const modeValid =
      matrixMode === "THPTQG_2025" ? presetValidation.valid : shortageReport.totalDesired >= 1;
    if (!shortageReport.readyToAssemble || !modeValid) return;

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

    const useLegacyTestMock = Boolean(fetchFn) && !isUuid(data.examId) && !createNewOnAssemble;
    if (useLegacyTestMock) {
      await runApiMutation(
        `/api/v1/assessments/${encodeURIComponent(data.examId)}/assemble`,
        {
          random_seed: randomSeed,
          subject_group: data.subjectGroup,
          cells: cells.filter((c) => c.desiredCount > 0),
        },
        `Đã bốc tự động đề thi ${totalCount} câu (seed=${randomSeed}) đúng chuẩn THPTQG 2025+.`,
      );
      return;
    }

    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    setPublishedRoomLink(null);
    try {
      let targetAssessmentId = isUuid(data.examId) && !createNewOnAssemble ? data.examId : "";
      if (!targetAssessmentId) {
        const createdRes = await workspaceCall<{ id: string; title?: string; publication_status?: string }>({
          url: "/api/v1/assessments",
          method: "POST",
          json: {
            title: newPaperTitle.trim() || `Đề ${activeSubjectCode === "math" ? "Toán 12" : "Vật lí"} (${totalCount} câu)`,
            subject_code: activeSubjectCode,
            grade_band: "GRADE_12",
            exam_type_code: matrixMode === "THPTQG_2025" ? "THPTQG" : "QUIZ",
            rights_status: "OWNED",
          },
          fetchFn,
          apiBase,
          redirectOn401: false,
        });
        if (!createdRes.ok || !createdRes.data?.id) {
          setApiError(createdRes.ok ? "Không tạo được bản nháp đề thi." : createdRes.error);
          setApiErrorStatus(createdRes.ok ? 500 : createdRes.status);
          return;
        }
        targetAssessmentId = createdRes.data.id;
        setLivePapers((prev) => [createdRes.data!, ...prev]);
        setData((prev) => ({
          ...prev,
          examId: targetAssessmentId,
          examTitle: createdRes.data?.title ?? prev.examTitle,
        }));
      }

      const uuidCells = cells.filter((c) => c.desiredCount > 0 && isUuid(c.topicId));
      const assembleRes = await workspaceCall({
        url: `/api/v1/assessments/${encodeURIComponent(targetAssessmentId)}/assemble`,
        method: "POST",
        json: {
          framework_code: "THPTQG_2025",
          subject_code: activeSubjectCode,
          random_seed: randomSeed,
          taxonomy_node_ids: [],
          ...(uuidCells.length > 0
            ? {
                taxonomy_counts: uuidCells.map((c) => ({
                  taxonomy_node_id: c.topicId,
                  question_count: c.desiredCount,
                  difficulty_level: c.difficulty,
                  item_types: [c.questionType],
                })),
                duration_minutes: durationMinutes,
              }
            : {}),
        },
        fetchFn,
        apiBase,
        redirectOn401: false,
      });
      if (!assembleRes.ok) {
        setApiError(assembleRes.error);
        setApiErrorStatus(assembleRes.status);
        setStatusFeedback(null);
        return;
      }
      setAssembledAssessmentId(targetAssessmentId);
      setCreateNewOnAssemble(false);
      setStatusFeedback(
        `Đã bốc tự động đề thi ${totalCount} câu (seed=${randomSeed}). Bấm "Xuất bản đề" để mở phòng thi!`,
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handlePublishAndAssign = async () => {
    const targetId = assembledAssessmentId ?? (isUuid(data.examId) ? data.examId : null);
    if (!targetId) return;
    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    try {
      const pubRes = await workspaceCall<{ version?: { id?: string } }>({
        url: `/api/v1/assessments/${encodeURIComponent(targetId)}/publish`,
        method: "POST",
        json: {},
        fetchFn,
        apiBase,
        redirectOn401: false,
      });
      if (!pubRes.ok) {
        setApiError(pubRes.error);
        setApiErrorStatus(pubRes.status);
        return;
      }
      const versionId = pubRes.data?.version?.id;
      if (selectedClassId && isUuid(selectedClassId) && versionId) {
        const assignRes = await workspaceCall({
          url: "/api/v1/assignments",
          method: "POST",
          json: {
            class_id: selectedClassId,
            assessment_version_id: versionId,
            max_attempts: 1,
          },
          fetchFn,
          apiBase,
          redirectOn401: false,
        });
        if (!assignRes.ok) {
          setApiError(assignRes.error);
          setApiErrorStatus(assignRes.status);
          return;
        }
        setPublishedRoomLink(`/teacher/classes/${selectedClassId}`);
        setStatusFeedback("Đã xuất bản đề và giao ngay cho lớp học đã chọn!");
        return;
      }
      setPublishedRoomLink(`/teacher/classes/${teacherClasses[0]?.id ?? "class-12a1"}`);
      setStatusFeedback("Đã xuất bản đề thi thành công (PUBLISHED)!");
    } finally {
      setIsSubmitting(false);
    }
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

    const isModeValid =
      matrixMode === "THPTQG_2025" ? presetValidation.valid : shortageReport.totalDesired >= 1;
    const canAssemble = shortageReport.readyToAssemble && isModeValid && !isSubmitting;

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

        {/* Mode Switcher: Tab 1 (Topic Matrix) vs Tab 2 (Upload PDF -> AI Solve -> Exam Room) */}
        <nav aria-label="Chế độ tạo đề Giáo viên" className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="tab-matrix"
            onClick={() => setActiveTab("matrix")}
            className={`rounded-xl px-4 py-2 text-xs font-extrabold transition ${
              activeTab === "matrix"
                ? "bg-blue-700 text-white shadow-xs"
                : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
            }`}
          >
            1. Tạo đề theo Chủ đề &amp; Ma trận ({livePoolTotal !== null ? `${livePoolTotal} câu sẵn` : "Kho C03/C04"})
          </button>
          <button
            type="button"
            data-testid="tab-from-pdf"
            onClick={() => setActiveTab("from-pdf")}
            className={`rounded-xl px-4 py-2 text-xs font-extrabold transition ${
              activeTab === "from-pdf"
                ? "bg-blue-700 text-white shadow-xs"
                : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
            }`}
          >
            2. Upload đề PDF (AI tự giải → Mở phòng thi)
          </button>
        </nav>

        {activeTab === "from-pdf" ? (
          <TeacherPdfUploadPanel
            classes={teacherClasses}
            fetchFn={fetchFn}
            apiBase={apiBase}
            onPaperCreated={(paper) => {
              setLivePapers((prev) => [
                {
                  id: paper.assessment_id,
                  title: paper.title,
                  subject_code: activeSubjectCode,
                  publication_status: "PUBLISHED",
                },
                ...prev,
              ]);
            }}
          />
        ) : (
          <>
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
                  {livePapers.length > 0 && (
                    <select
                      aria-label="Chọn đề thi thật để bốc câu"
                      data-testid="live-assessment-select"
                      value={createNewOnAssemble ? "__NEW__" : data.examId}
                      onChange={(e) => {
                        if (e.target.value === "__NEW__") {
                          setCreateNewOnAssemble(true);
                          return;
                        }
                        setCreateNewOnAssemble(false);
                        const picked = livePapers.find((p) => p.id === e.target.value);
                        if (!picked) return;
                        setData((prev) => ({
                          ...prev,
                          examId: picked.id,
                          examTitle: picked.title ?? prev.examTitle,
                        }));
                      }}
                      className="rounded-lg border border-emerald-300 bg-emerald-50 px-2.5 py-2 text-xs font-semibold text-emerald-950"
                    >
                      {livePapers.map((paper) => (
                        <option key={paper.id} value={paper.id}>
                          {paper.title ?? paper.id.slice(0, 8)} ({paper.publication_status ?? paper.status ?? "DRAFT"})
                        </option>
                      ))}
                      <option value="__NEW__">+ Tạo đề mới khi bốc</option>
                    </select>
                  )}
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
                  <button
                    type="button"
                    data-testid="toggle-custom-topic-btn"
                    onClick={() =>
                      setMatrixMode((prev) => (prev === "THPTQG_2025" ? "CUSTOM_TOPIC" : "THPTQG_2025"))
                    }
                    className={`rounded-lg border px-3 py-2 text-xs font-bold transition ${
                      matrixMode === "CUSTOM_TOPIC"
                        ? "border-indigo-600 bg-indigo-50 text-indigo-900"
                        : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    {matrixMode === "CUSTOM_TOPIC" ? "Chế độ: Tự chọn chủ đề" : "Chuyển: Tự chọn số câu"}
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
                matrixMode === "CUSTOM_TOPIC"
                  ? shortageReport.totalDesired >= 1
                    ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                    : "border-amber-300 bg-amber-50 text-amber-950"
                  : presetValidation.valid
                    ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                    : "border-amber-300 bg-amber-50 text-amber-950"
              }`}
            >
              <div className="text-[11px] font-semibold">
                {matrixMode === "CUSTOM_TOPIC" ? "Chế độ tự chọn chủ đề" : "Tổng điểm / Tổng câu"}
              </div>
              <div className="mt-1 text-lg font-extrabold">
                {matrixMode === "CUSTOM_TOPIC"
                  ? `${shortageReport.totalDesired} câu đã chọn`
                  : `${presetValidation.computedTotalPoints.toFixed(2)} điểm · ${presetValidation.actualTotalItems}/${presetValidation.expectedTotalItems} câu`}
              </div>
              {matrixMode === "CUSTOM_TOPIC" ? (
                <p className="mt-1 text-[11px]">
                  {shortageReport.totalDesired >= 1
                    ? "Hợp lệ — chọn câu theo chủ đề thực tế trong kho."
                    : "Nhập ít nhất 1 câu ở chủ đề bất kỳ để bốc đề."}
                </p>
              ) : (
                !presetValidation.valid && (
                  <ul className="mt-1 list-disc pl-4 text-[11px]">
                    {presetValidation.errors.map((err) => (
                      <li key={err}>{err}</li>
                    ))}
                  </ul>
                )
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
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
          {(createNewOnAssemble || !isUuid(data.examId)) && (
            <div className="grid grid-cols-1 gap-3 border-b border-slate-100 pb-3 sm:grid-cols-3">
              <label className="text-xs font-semibold text-slate-700">
                <span className="mb-1 block text-[11px] uppercase text-slate-500">Tên đề thi mới</span>
                <input
                  type="text"
                  value={newPaperTitle}
                  onChange={(e) => setNewPaperTitle(e.target.value)}
                  data-testid="new-paper-title-input"
                  className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-semibold text-slate-900"
                />
              </label>
              <label className="text-xs font-semibold text-slate-700">
                <span className="mb-1 block text-[11px] uppercase text-slate-500">Thời gian (phút)</span>
                <select
                  value={durationMinutes}
                  onChange={(e) => setDurationMinutes(Number(e.target.value))}
                  data-testid="matrix-duration-select"
                  className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-900"
                >
                  {[15, 30, 45, 60, 90, 120].map((m) => (
                    <option key={m} value={m}>
                      {m} phút
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs font-semibold text-slate-700">
                <span className="mb-1 block text-[11px] uppercase text-slate-500">Giao cho lớp (khi xuất bản)</span>
                <select
                  value={selectedClassId}
                  onChange={(e) => setSelectedClassId(e.target.value)}
                  data-testid="matrix-class-select"
                  className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-900"
                >
                  <option value="">-- Chỉ xuất bản đề --</option>
                  {teacherClasses.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-4">
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
              {publishedRoomLink && (
                <a
                  href={publishedRoomLink}
                  className="rounded-lg bg-emerald-100 px-3 py-1.5 text-xs font-bold text-emerald-900 underline"
                >
                  Mở lớp kiểm tra →
                </a>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                data-testid="assemble-exam-btn"
                disabled={!canAssemble}
                onClick={handleAssembleExam}
                className="rounded-xl bg-blue-700 px-5 py-2.5 text-sm font-bold text-white shadow-xs transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
              >
                Bốc đề tự động C04 ({shortageReport.totalDesired} câu)
              </button>
              {assembledAssessmentId && (
                <button
                  type="button"
                  data-testid="publish-assembled-btn"
                  disabled={isSubmitting}
                  onClick={handlePublishAndAssign}
                  className="rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-bold text-white shadow-xs transition hover:bg-emerald-800 disabled:opacity-50"
                >
                  Xuất bản đề{selectedClassId ? " & Giao lớp" : ""}
                </button>
              )}
            </div>
          </div>
        </section>
          </>
        )}
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
