"use client";

import React, { useMemo, useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
} from "@/components/workspace/WorkspaceStateViews";
import type { UserTier } from "@/lib/ui-state-machine";
import { H2EduHeader } from "./H2EduHeader";
import { DEFAULT_ERROR_NOTES, type StudyHubErrorNoteItem } from "./types";

export interface ErrorNotebookShellProps {
  initialNotes?: readonly StudyHubErrorNoteItem[];
  initialUserTier?: UserTier;
  isLoading?: boolean;
  errorCode?: string | null;
}

export function ErrorNotebookShell({
  initialNotes = DEFAULT_ERROR_NOTES,
  initialUserTier = "FREE",
  isLoading = false,
  errorCode = null,
}: ErrorNotebookShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [notes, setNotes] = useState<readonly StudyHubErrorNoteItem[]>(initialNotes);
  const [filter, setFilter] = useState<"ALL" | "ACTIVE" | "RESOLVED">("ALL");
  const [selectedOptions, setSelectedOptions] = useState<Record<string, string>>({});
  const [lastAttemptMessage, setLastAttemptMessage] = useState<Record<string, string>>({});

  const visibleNotes = useMemo(() => {
    if (filter === "ALL") {
      return notes;
    }
    return notes.filter((n) => n.status === filter);
  }, [notes, filter]);

  const handleSubmitRemediation = (note: StudyHubErrorNoteItem) => {
    const picked = selectedOptions[note.id];
    if (!picked) {
      setLastAttemptMessage((prev) => ({
        ...prev,
        [note.id]: "Vui lòng chọn một phương án biến thể trước khi nộp.",
      }));
      return;
    }

    const isCorrect = picked === note.variantCorrectOptionKey;
    setNotes((prev) =>
      prev.map((item) => {
        if (item.id !== note.id) {
          return item;
        }
        const nextCount = isCorrect ? item.consecutiveCorrectCount + 1 : 0;
        const nextStatus: "ACTIVE" | "RESOLVED" = nextCount >= 2 ? "RESOLVED" : "ACTIVE";
        return {
          ...item,
          consecutiveCorrectCount: nextCount,
          status: nextStatus,
        };
      }),
    );

    if (isCorrect) {
      const nextCount = note.consecutiveCorrectCount + 1;
      setLastAttemptMessage((prev) => ({
        ...prev,
        [note.id]:
          nextCount >= 2
            ? "Đúng câu biến thể! Đã đạt 2 lần đúng liên tiếp — chuyển sang RESOLVED."
            : "Đúng 1 câu biến thể (1/2). Cần đúng liên tiếp thêm 1 câu tương tự để đóng lỗi.",
      }));
    } else {
      setLastAttemptMessage((prev) => ({
        ...prev,
        [note.id]: "Chưa chính xác — chuỗi liên tiếp đặt lại về 0/2. Hãy xem lại chẩn đoán và thử lại.",
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
