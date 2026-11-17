"use client";

import React, { useEffect, useMemo, useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
} from "@/components/workspace/WorkspaceStateViews";
import { hasAuthToken } from "@/lib/auth-client";
import type { UserTier } from "@/lib/ui-state-machine";
import { isUuid, shouldHydrateLive, shouldSyncLiveEntity, workspaceCall } from "@/lib/workspace-api";
import { H2EduHeader } from "./H2EduHeader";
import { DEFAULT_ERROR_NOTES, type StudyHubErrorNoteItem } from "./types";

export interface ErrorNotebookShellProps {
  initialNotes?: readonly StudyHubErrorNoteItem[];
  initialUserTier?: UserTier;
  isLoading?: boolean;
  errorCode?: string | null;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function ErrorNotebookShell({
  initialNotes = DEFAULT_ERROR_NOTES,
  initialUserTier = "FREE",
  isLoading = false,
  errorCode = null,
  fetchFn,
  apiBase = "",
}: ErrorNotebookShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [notes, setNotes] = useState<readonly StudyHubErrorNoteItem[]>(initialNotes);
  const [filter, setFilter] = useState<"ALL" | "ACTIVE" | "RESOLVED">("ALL");
  const [selectedOptions, setSelectedOptions] = useState<Record<string, string>>({});
  const [lastAttemptMessage, setLastAttemptMessage] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);

  useEffect(() => {
    if (!shouldHydrateLive({ hasInitialData: initialNotes !== DEFAULT_ERROR_NOTES, apiBase })) {
      return;
    }
    let active = true;
    void workspaceCall<
      Array<{
        id: string;
        question_version_id?: string;
        wrong_count?: number;
        consecutive_correct_count?: number;
        status?: string;
      }>
    >({
      url: "/api/v1/error-notes",
      method: "GET",
      fetchFn,
      apiBase,
      redirectOn401: false,
    }).then((result) => {
      if (!active) return;
      if (!result.ok) {
        if (result.status !== 404) {
          setApiError(result.error);
          setApiErrorStatus(result.status);
        }
        return;
      }
      if (Array.isArray(result.data) && result.data.length > 0) {
        setNotes(
          result.data.map((n, idx) => ({
            id: n.id,
            questionCode: String(n.question_version_id ?? `Q-${idx + 1}`).slice(0, 12),
            topicTitle: "Sổ tay câu sai (D05)",
            summaryLatex: `Đã ghi nhận ${n.wrong_count ?? 1} lần làm sai — hoàn thành 2 câu biến thể đúng liên tiếp.`,
            diagnosis: "Đồng bộ trực tiếp từ backend /api/v1/error-notes.",
            consecutiveCorrectCount: n.consecutive_correct_count ?? 0,
            status: n.status === "RESOLVED" ? "RESOLVED" : "ACTIVE",
            variantDrillPrompt: "Chọn đáp án chính xác cho biến thể ôn tập:",
            variantCorrectOptionKey: "A",
            variantOptions: [
              { key: "A", label: "Đáp án đúng" },
              { key: "B", label: "Phương án nhiễu" },
            ],
          })),
        );
      }
    });
    return () => {
      active = false;
    };
  }, [apiBase, fetchFn, initialNotes]);

  const visibleNotes = useMemo(() => {
    if (filter === "ALL") {
      return notes;
    }
    return notes.filter((n) => n.status === filter);
  }, [notes, filter]);

  const handleSubmitRemediation = async (note: StudyHubErrorNoteItem) => {
    const picked = selectedOptions[note.id];
    if (!picked) {
      setLastAttemptMessage((prev) => ({
        ...prev,
        [note.id]: "Vui lòng chọn một phương án biến thể trước khi nộp.",
      }));
      return;
    }

    const isCorrect = picked === note.variantCorrectOptionKey;
    let nextCount = isCorrect ? note.consecutiveCorrectCount + 1 : 0;
    let nextStatus: "ACTIVE" | "RESOLVED" = nextCount >= 2 ? "RESOLVED" : "ACTIVE";
    const syncLive = shouldSyncLiveEntity({
      fetchFn,
      apiBase,
      entityIds: [note.id],
    });

    setApiError(null);
    setApiErrorStatus(null);

    if (syncLive) {
      const result = await workspaceCall<{
        consecutive_correct_count?: number;
        status?: "ACTIVE" | "RESOLVED";
      }>({
        url: `/api/v1/error-notes/${encodeURIComponent(note.id)}/resolve`,
        method: "POST",
        json: { is_correct: isCorrect },
        fetchFn,
        apiBase,
      });
      if (!result.ok) {
        setApiError(result.error);
        setApiErrorStatus(result.status);
        return;
      }
      if (typeof result.data?.consecutive_correct_count === "number") {
        nextCount = result.data.consecutive_correct_count;
      }
      if (result.data?.status === "ACTIVE" || result.data?.status === "RESOLVED") {
        nextStatus = result.data.status;
      }
    }

    setNotes((prev) =>
      prev.map((item) =>
        item.id !== note.id
          ? item
          : {
              ...item,
              consecutiveCorrectCount: nextCount,
              status: nextStatus,
            },
      ),
    );

    const demoNoteSuffix = !syncLive && !isUuid(note.id) && hasAuthToken() ? " (dữ liệu mẫu demo cục bộ)" : "";
    if (isCorrect) {
      setLastAttemptMessage((prev) => ({
        ...prev,
        [note.id]:
          (nextCount >= 2
            ? "Đúng câu biến thể! Đã đạt 2 lần đúng liên tiếp — chuyển sang RESOLVED."
            : "Đúng 1 câu biến thể (1/2). Cần đúng liên tiếp thêm 1 câu tương tự để đóng lỗi.") + demoNoteSuffix,
      }));
    } else {
      setLastAttemptMessage((prev) => ({
        ...prev,
        [note.id]:
          "Chưa chính xác — chuỗi liên tiếp đặt lại về 0/2. Hãy xem lại chẩn đoán và thử lại." + demoNoteSuffix,
      }));
    }
  };

  return (
    <ErrorBoundary errorCode={errorCode}>
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <H2EduHeader activeRoute="so-tay-cau-sai" userTier={userTier} onTierChange={setUserTier} />

        {isLoading ? (
          <LoadingSkeleton title="Đang tải Sổ tay câu sai..." />
        ) : notes.length === 0 ? (
          <EmptyState
            title="Sổ tay câu sai đang trống"
            description="Bạn chưa ghi nhận câu hỏi làm sai nào cần ôn luyện khắc phục."
          />
        ) : (
          <main data-testid="error-notebook-main" className="mx-auto max-w-5xl space-y-6 px-4 py-6">
            {apiError && (
              <WorkspaceApiAlert
                message={apiError}
                status={apiErrorStatus}
                onDismiss={() => setApiError(null)}
              />
            )}
            <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-blue-700">
                  D05 · Sổ tay câu sai &amp; Luyện tập khắc phục (Remediation)
                </div>
                <h1 className="mt-1 text-2xl font-extrabold text-slate-900">
                  Khắc phục lỗ hổng qua câu biến thể tương tự
                </h1>
                <p className="mt-1 text-sm text-slate-600">
                  Mỗi mục chỉ được đánh dấu <strong>RESOLVED</strong> khi bạn làm đúng liên tiếp ít nhất <strong>2 câu biến thể</strong> cùng dạng.
                </p>
              </div>

              <div className="flex gap-1.5" role="group" aria-label="Lọc trạng thái câu sai">
                {(["ALL", "ACTIVE", "RESOLVED"] as const).map((statusKey) => (
                  <button
                    key={statusKey}
                    type="button"
                    data-testid={`filter-error-${statusKey}`}
                    onClick={() => setFilter(statusKey)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-bold transition ${
                      filter === statusKey
                        ? "bg-blue-700 text-white"
                        : "border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                    }`}
                  >
                    {statusKey === "ALL"
                      ? `Tất cả (${notes.length})`
                      : statusKey === "ACTIVE"
                        ? "Đang khắc phục"
                        : "Đã giải quyết"}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-4">
              {visibleNotes.map((note) => (
                <article
                  key={note.id}
                  data-testid={`error-note-card-${note.id}`}
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <span className="font-mono text-xs font-bold text-slate-500">{note.questionCode}</span>
                      <h2 className="text-base font-bold text-slate-900">{note.topicTitle}</h2>
                    </div>
                    <span
                      data-testid={`error-note-status-${note.id}`}
                      className={`rounded-full px-3 py-1 text-xs font-bold ${
                        note.status === "RESOLVED"
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-amber-100 text-amber-900"
                      }`}
                    >
                      {note.status} · {note.consecutiveCorrectCount}/2 đúng liên tiếp
                    </span>
                  </div>

                  <p className="mt-2 text-sm text-slate-800">
                    <SafeKatexText text={note.summaryLatex} />
                  </p>
                  <p className="mt-1 text-xs text-slate-600">
                    <strong>Chẩn đoán:</strong> <SafeKatexText text={note.diagnosis} />
                  </p>

                  <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <div className="text-xs font-bold uppercase tracking-wide text-blue-700">
                      Câu hỏi biến thể khắc phục
                    </div>
                    <div className="mt-1.5 text-sm text-slate-900">
                      <SafeKatexText text={note.variantDrillPrompt} />
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                      {note.variantOptions.map((opt) => {
                        const selected = selectedOptions[note.id] === opt.key;
                        return (
                          <button
                            key={opt.key}
                            type="button"
                            data-testid={`drill-opt-${note.id}-${opt.key}`}
                            onClick={() =>
                              setSelectedOptions((prev) => ({ ...prev, [note.id]: opt.key }))
                            }
                            className={`rounded-lg border px-3 py-1.5 text-left text-xs font-semibold ${
                              selected
                                ? "border-blue-600 bg-blue-50 text-blue-900"
                                : "border-slate-200 bg-white text-slate-800 hover:bg-slate-100"
                            }`}
                          >
                            {opt.key}. <SafeKatexText text={opt.label} />
                          </button>
                        );
                      })}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        data-testid={`submit-drill-${note.id}`}
                        onClick={() => handleSubmitRemediation(note)}
                        className="rounded-lg bg-blue-700 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-blue-800"
                      >
                        Kiểm tra câu biến thể
                      </button>
                      {lastAttemptMessage[note.id] && (
                        <span
                          data-testid={`drill-feedback-${note.id}`}
                          className="text-xs font-semibold text-slate-700"
                        >
                          {lastAttemptMessage[note.id]}
                        </span>
                      )}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
