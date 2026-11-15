"use client";

import React, { useEffect, useMemo, useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
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
import { TeacherHeader } from "./TeacherHeader";
import {
  createDemoTeacherClassData,
  type CopilotRemediationCluster,
  type StudentGradeRow,
  type TeacherClassLmsData,
} from "./types";

export interface TeacherClassLmsShellProps {
  classId: string;
  initialData?: TeacherClassLmsData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function buildGradebookCsv(students: readonly StudentGradeRow[]): string {
  const header = ["student_code", "student_name", "status", "submitted_at", "score"].join(",");
  const rows = students.map((s) => {
    const status = !s.submitted ? "NOT_SUBMITTED" : s.isLate ? "LATE" : "ON_TIME";
    const safeName = `"${s.studentName.replace(/"/g, '""')}"`;
    return [s.studentCode, safeName, status, s.submittedAt ?? "", s.score !== null ? s.score.toFixed(2) : ""].join(
      ",",
    );
  });
  return [header, ...rows].join("\n");
}

export function TeacherClassLmsShell({
  classId,
  initialData,
  initialUserTier = "TEACHER_PRO",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: TeacherClassLmsShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [classData, setClassData] = useState<TeacherClassLmsData>(
    () => initialData ?? createDemoTeacherClassData(classId),
  );
  const [filterMode, setFilterMode] = useState<"ALL" | "SUBMITTED" | "UNSUBMITTED" | "LATE">("ALL");
  const [confirmingClusterId, setConfirmingClusterId] = useState<string | null>(null);
  const [statusFeedback, setStatusFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [lastExportCsv, setLastExportCsv] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  useEffect(() => {
    if (!shouldHydrateLive({ hasInitialData: Boolean(initialData), apiBase })) return;
    let active = true;
    void workspaceCall<{
      classroom?: { name?: string; join_code?: string; title?: string };
      members?: Array<{
        membership_id?: string;
        display_name?: string | null;
        role_in_class?: string;
        status?: string;
      }>;
    }>({
      url: `/api/v1/classes/${encodeURIComponent(classId)}`,
      method: "GET",
      fetchFn,
      apiBase,
      redirectOn401: false,
    }).then((result) => {
      if (!active) return;
      if (!result.ok) {
        if (result.status !== 404 && result.status !== 422) {
          setApiError(result.error);
          setApiErrorStatus(result.status);
        }
        return;
      }
      const remote = result.data;
      if (!remote?.classroom) return;
      const studentMembers = (remote.members ?? [])
        .filter((m) => (m.role_in_class ?? "STUDENT") === "STUDENT")
        .map<StudentGradeRow>((m, idx) => ({
          studentId: m.membership_id ?? `mem-${idx + 1}`,
          studentCode: (m.membership_id ?? `HS${idx + 1}`).slice(0, 8),
          studentName: m.display_name || `Học sinh ${idx + 1}`,
          submitted: false,
          submittedAt: null,
          isLate: false,
          score: null,
          attemptId: null,
          weakTopicIds: [],
        }));
      setClassData((prev) => ({
        ...prev,
        className: remote.classroom?.name || remote.classroom?.title || prev.className,
        inviteCode: remote.classroom?.join_code || prev.inviteCode,
        ...(studentMembers.length > 0 ? { students: studentMembers } : {}),
      }));
    });
    return () => {
      active = false;
    };
  }, [apiBase, classId, fetchFn, initialData]);

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: classData.students.length,
        requiresProAsset: classData.requiresProAsset,
        userTier,
        previewPdfUrl: classData.previewPdfUrl,
        rawAssetUrl: classData.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [classData, userTier, initialUiOverrides, conflictDismissed],
  );

  const unsubmittedStudents = useMemo(
    () => classData.students.filter((s) => !s.submitted),
    [classData.students],
  );

  const filteredStudents = useMemo(() => {
    if (filterMode === "SUBMITTED") return classData.students.filter((s) => s.submitted);
    if (filterMode === "UNSUBMITTED") return unsubmittedStudents;
    if (filterMode === "LATE") return classData.students.filter((s) => s.submitted && s.isLate);
    return classData.students;
  }, [classData.students, unsubmittedStudents, filterMode]);

  const distributionBuckets = useMemo(() => {
    const scored = classData.students.filter((s): s is StudentGradeRow & { score: number } => s.score !== null);
    const defs = [
      { label: "< 5.0", min: 0, max: 4.999 },
      { label: "5.0–6.4", min: 5.0, max: 6.499 },
      { label: "6.5–7.9", min: 6.5, max: 7.999 },
      { label: "8.0–8.9", min: 8.0, max: 8.999 },
      { label: "9.0–10", min: 9.0, max: 10.01 },
    ];
    return defs.map((d) => {
      const count = scored.filter((s) => s.score >= d.min && s.score <= d.max).length;
      const pct = scored.length > 0 ? Math.round((count / scored.length) * 100) : 0;
      return { ...d, count, pct };
    });
  }, [classData.students]);

  const handleExportCsv = async () => {
    setApiError(null);
    setApiErrorStatus(null);
    const localCsv = buildGradebookCsv(classData.students);
    setLastExportCsv(localCsv);
    const result = await workspaceCall<string>({
      url: `/api/v1/classes/${encodeURIComponent(classId)}/gradebook/export`,
      method: "GET",
      fetchFn,
      apiBase,
    });
    if (!result.ok) {
      setApiError(result.error);
      setApiErrorStatus(result.status);
      setStatusFeedback(null);
      return;
    }
    if (typeof result.data === "string" && result.data.includes(",")) {
      setLastExportCsv(result.data);
    }
    setStatusFeedback(`Đã xuất bảng điểm CSV gồm ${classData.students.length} học sinh.`);
  };

  const handleConfirmRemediation = async (cluster: CopilotRemediationCluster) => {
    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    try {
      const result = await workspaceCall({
        url: "/api/v1/assignments",
        method: "POST",
        json: {
          class_id: classData.classId,
          topic_id: cluster.topicId,
          target_student_ids: cluster.studentIds,
          question_count: cluster.recommendedQuestionCount,
          source_copilot_cluster_id: cluster.clusterId,
        },
        fetchFn,
        apiBase,
      });
      if (!result.ok) {
        setApiError(result.error);
        setApiErrorStatus(result.status);
        setStatusFeedback(null);
        return;
      }
      setClassData((prev) => ({
        ...prev,
        copilotClusters: prev.copilotClusters.map((c) =>
          c.clusterId === cluster.clusterId ? { ...c, assigned: true } : c,
        ),
      }));
      setConfirmingClusterId(null);
      setStatusFeedback(
        `Đã giao phiếu bài tập khắc phục ${cluster.recommendedQuestionCount} câu chuyên đề "${cluster.topicName}" cho ${cluster.studentNames.length} học sinh.`,
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderMainContent = () => {
    if (uiResolution.kind === "LoadingSkeleton") {
      return <LoadingSkeleton title="Đang tải sổ điểm lớp học..." />;
    }
    if (uiResolution.kind === "ErrorBoundary") {
      return (
        <ErrorBoundary
          errorCode={initialUiOverrides?.fatalErrorCode ?? "CLASS_LMS_ERROR"}
          errorMessage={uiResolution.bannerMessage}
        />
      );
    }
    if (uiResolution.kind === "EmptyState") {
      return (
        <EmptyState
          title="Lớp học chưa có học sinh"
          description={`Chia sẻ mã mời ${classData.inviteCode} hoặc import danh sách CSV để bắt đầu.`}
        />
      );
    }
    if (uiResolution.kind === "LockedProPreview") {
      return (
        <LockedProPreview
          title="Phân tích phương án nhiễu & AI Copilot yêu cầu TEACHER_PRO"
          previewPdfUrl={uiResolution.exposedAssetUrl}
          onUpgradeClick={() => setUserTier("TEACHER_PRO")}
        />
      );
    }

    const submittedCount = classData.students.filter((s) => s.submitted).length;

    return (
      <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-5">
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

        {/* Header summary strip */}
        <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <div>
            <span className="text-[11px] font-bold uppercase tracking-wider text-blue-700">
              F01–F03 Gradebook &amp; Class Analytics · E06 Teacher AI Copilot
            </span>
            <h1 className="mt-1 text-xl font-extrabold text-slate-900">
              {classData.className} — {classData.activeAssessmentTitle}
            </h1>
            <p className="mt-1 text-xs text-slate-600">
              {classData.subjectName} · Mã mời lớp: <code className="font-bold">{classData.inviteCode}</code> · Đã
              nộp: {submittedCount}/{classData.students.length} học sinh
            </p>
          </div>
          <div className="flex items-center gap-2">
            {statusFeedback && (
              <span
                role="status"
                data-testid="class-lms-feedback"
                className="rounded-lg bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-900"
              >
                {statusFeedback}
              </span>
            )}
            <button
              type="button"
              data-testid="export-gradebook-csv-btn"
              onClick={handleExportCsv}
              className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-bold text-slate-800 transition hover:bg-slate-50"
            >
              Xuất CSV Bảng điểm
            </button>
          </div>
        </section>

        {/* Distribution + Unsubmitted Students */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          <section
            data-testid="score-distribution-panel"
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs lg:col-span-7"
          >
            <h2 className="text-sm font-extrabold text-slate-900">Phổ điểm lớp học</h2>
            <p className="mt-0.5 text-xs text-slate-500">Tổng hợp trên các lượt nộp bài đã chấm điểm C07.</p>
            <div className="mt-4 space-y-2.5">
              {distributionBuckets.map((bucket) => (
                <div key={bucket.label} className="flex items-center gap-3 text-xs">
                  <span className="w-16 font-bold text-slate-700">{bucket.label}</span>
                  <div className="h-5 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-blue-600 transition-all"
                      style={{ width: `${Math.max(bucket.pct, bucket.count > 0 ? 8 : 0)}%` }}
                    />
                  </div>
                  <span className="w-20 text-right font-semibold text-slate-700">
                    {bucket.count} HS ({bucket.pct}%)
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section
            data-testid="unsubmitted-students-panel"
            className="rounded-2xl border border-amber-200 bg-amber-50/70 p-5 shadow-xs lg:col-span-5"
          >
            <h2 className="text-sm font-extrabold text-amber-950">
              Học sinh chưa nộp bài ({unsubmittedStudents.length})
            </h2>
            <p className="mt-0.5 text-xs text-amber-900">
              Theo dõi trạng thái làm bài (F02 Assignment Deadline) mà không xóa lịch sử rời lớp (F01 Soft-Delete).
            </p>
            <ul className="mt-3 space-y-2">
              {unsubmittedStudents.map((stu) => (
                <li
                  key={stu.studentId}
                  className="flex items-center justify-between rounded-xl border border-amber-200 bg-white px-3 py-2 text-xs"
                >
                  <div>
                    <div className="font-bold text-slate-900">{stu.studentName}</div>
                    <div className="text-[11px] text-slate-500">Mã HS: {stu.studentCode}</div>
                  </div>
                  <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-[11px] font-bold text-amber-900">
                    Chưa nộp bài
                  </span>
                </li>
              ))}
              {unsubmittedStudents.length === 0 && (
                <li className="rounded-xl bg-white px-3 py-2 text-xs font-semibold text-emerald-700">
                  100% học sinh trong lớp đã hoàn thành bài thi!
                </li>
              )}
            </ul>
          </section>
        </div>

        {/* Teacher AI Copilot (E06) */}
        <section
          data-testid="teacher-copilot-panel"
          className="rounded-2xl border border-indigo-200 bg-indigo-50/60 p-5 shadow-xs"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider text-indigo-700">
                Teacher AI Copilot (E06 · Strict Class RLS)
              </span>
              <h2 className="mt-0.5 text-base font-extrabold text-slate-900">
                Phân cụm lỗi sai &amp; Đề xuất phiếu bài tập phụ đạo (cần GV xác nhận)
              </h2>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4">
            {classData.copilotClusters.map((cluster) => (
              <div
                key={cluster.clusterId}
                className="rounded-xl border border-indigo-200 bg-white p-4 shadow-2xs"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-extrabold text-slate-900">{cluster.topicName}</div>
                    <p className="mt-1 text-xs text-slate-700">{cluster.summary}</p>
                    <p className="mt-1 text-xs font-semibold text-indigo-800">
                      Nhóm học sinh ({cluster.studentNames.length}): {cluster.studentNames.join(", ")}
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-2">
                    {cluster.assigned ? (
                      <span
                        data-testid={`remediation-assigned-badge-${cluster.clusterId}`}
                        className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-800"
                      >
                        Đã giao phiếu {cluster.recommendedQuestionCount} câu (F02)
                      </span>
                    ) : confirmingClusterId === cluster.clusterId ? (
                      <div className="flex items-center gap-2 rounded-lg border border-indigo-300 bg-indigo-50 p-2">
                        <span className="text-xs font-semibold text-indigo-950">Xác nhận giao bài F02?</span>
                        <button
                          type="button"
                          data-testid={`confirm-remediation-btn-${cluster.clusterId}`}
                          disabled={isSubmitting}
                          onClick={() => handleConfirmRemediation(cluster)}
                          className="rounded bg-indigo-700 px-2.5 py-1 text-xs font-bold text-white hover:bg-indigo-800"
                        >
                          Xác nhận giao
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmingClusterId(null)}
                          className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700"
                        >
                          Hủy
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        data-testid={`assign-remediation-btn-${cluster.clusterId}`}
                        onClick={() => setConfirmingClusterId(cluster.clusterId)}
                        className="rounded-lg bg-indigo-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-indigo-800"
                      >
                        Giao phiếu bài tập khắc phục {cluster.recommendedQuestionCount} câu cho nhóm học sinh này
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Top 5 Item Analysis + Distractor Breakdown */}
        <section
          data-testid="item-analysis-panel"
          className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
        >
          <h2 className="text-base font-extrabold text-slate-900">
            Item Analysis — Top {classData.topMissedItems.length} câu lớp sai nhiều nhất &amp; Tỷ lệ chọn phương án
            nhiễu
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Công thức toán học render an toàn qua KaTeX (<code>trust: false, throwOnError: false</code>).
          </p>

          <div className="mt-4 space-y-4">
            {classData.topMissedItems.map((item, idx) => (
              <div
                key={item.questionId}
                data-testid={`item-analysis-card-${item.stableCode}`}
                className="rounded-xl border border-slate-200 bg-slate-50/70 p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-rose-700 px-2 py-0.5 text-xs font-extrabold text-white">
                      #{idx + 1} · {item.stableCode}
                    </span>
                    <span className="text-xs font-bold text-slate-700">{item.topicName}</span>
                  </div>
                  <div className="text-xs font-extrabold text-rose-700">
                    Tỷ lệ sai: {item.wrongRatePct}% · Bẫy nhiều nhất: Phương án {item.topDistractorKey}
                  </div>
                </div>
                <div className="mt-2 rounded-lg bg-white p-3 text-sm text-slate-900">
                  <SafeKatexText text={item.stemMd} />
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {item.distractors.map((dist) => (
                    <div
                      key={dist.key}
                      className={`rounded-lg border px-3 py-2 text-xs ${
                        dist.isCorrect
                          ? "border-emerald-300 bg-emerald-50 text-emerald-950"
                          : dist.key === item.topDistractorKey
                            ? "border-rose-300 bg-rose-50 text-rose-950"
                            : "border-slate-200 bg-white text-slate-700"
                      }`}
                    >
                      <div className="flex items-center justify-between font-bold">
                        <span>
                          {dist.key} {dist.isCorrect ? "(Đúng)" : dist.key === item.topDistractorKey ? "(Nhiễu cao)" : ""}
                        </span>
                        <span>{dist.selectedPct}%</span>
                      </div>
                      <div className="mt-1 text-[11px] text-slate-600">{dist.selectedCount} lượt chọn</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Gradebook Table */}
        <section
          data-testid="gradebook-table-panel"
          className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-extrabold text-slate-900">Bảng điểm chi tiết toàn lớp</h2>
            <div className="flex flex-wrap gap-1.5">
              {(["ALL", "SUBMITTED", "UNSUBMITTED", "LATE"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setFilterMode(mode)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-bold ${
                    filterMode === mode
                      ? "bg-slate-900 text-white"
                      : "border border-slate-300 bg-white text-slate-700"
                  }`}
                >
                  {mode === "ALL"
                    ? "Tất cả"
                    : mode === "SUBMITTED"
                      ? "Đã nộp"
                      : mode === "UNSUBMITTED"
                        ? "Chưa nộp"
                        : "Nộp muộn"}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2.5 font-bold">Mã HS</th>
                  <th className="px-3 py-2.5 font-bold">Họ tên</th>
                  <th className="px-3 py-2.5 font-bold">Trạng thái</th>
                  <th className="px-3 py-2.5 font-bold">Thời gian nộp</th>
                  <th className="px-3 py-2.5 font-bold text-right">Điểm (C07)</th>
                </tr>
              </thead>
              <tbody>
                {filteredStudents.map((stu) => (
                  <tr key={stu.studentId} className="border-b border-slate-100">
                    <td className="px-3 py-2.5 font-mono text-[11px] font-bold text-slate-700">
                      {stu.studentCode}
                    </td>
                    <td className="px-3 py-2.5 font-semibold text-slate-900">{stu.studentName}</td>
                    <td className="px-3 py-2.5">
                      {!stu.submitted ? (
                        <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-900">
                          Chưa nộp
                        </span>
                      ) : stu.isLate ? (
                        <span className="rounded bg-rose-100 px-2 py-0.5 text-[11px] font-bold text-rose-800">
                          Nộp muộn
                        </span>
                      ) : (
                        <span className="rounded bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-800">
                          Đúng hạn
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-slate-600">{stu.submittedAt ?? "—"}</td>
                    <td className="px-3 py-2.5 text-right font-extrabold text-slate-900">
                      {stu.score !== null ? stu.score.toFixed(2) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {lastExportCsv && (
            <pre
              data-testid="exported-csv-preview"
              className="mt-4 max-h-36 overflow-auto rounded-lg border border-slate-200 bg-slate-950 p-3 text-[11px] text-emerald-300"
            >
              {lastExportCsv}
            </pre>
          )}
        </section>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <TeacherHeader
        activeRoute="class-lms"
        classId={classId}
        userTier={userTier}
        onTierChange={setUserTier}
      />
      <main>{renderMainContent()}</main>
      {uiResolution.kind === "SequenceConflictModal" && (
        <SequenceConflictModal
          conflict={{
            questionVersionId: "class-lms-sync",
            questionLabel: "Sổ điểm Lớp học & Copilot",
            localPayload: "Nháp giao bài tập khắc phục",
            serverPayload: "Trạng thái lớp học mới nhất",
          }}
          onResolve={() => setConflictDismissed(true)}
        />
      )}
    </div>
  );
}
