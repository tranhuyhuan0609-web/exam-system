"use client";

import React from "react";
import type { SubmissionStateSnapshot } from "@/lib/exam-submission";

export interface ExamSubmitModalProps {
  open: boolean;
  totalQuestions: number;
  answeredCount: number;
  partialPart2Count: number;
  flaggedCount: number;
  submissionState: SubmissionStateSnapshot;
  onClose: () => void;
  onConfirmSubmit: () => void;
  onRetrySubmit: () => void;
  onForceSubmitWithUnsynced: () => void;
}

export function ExamSubmitModal({
  open,
  totalQuestions,
  answeredCount,
  partialPart2Count,
  flaggedCount,
  submissionState,
  onClose,
  onConfirmSubmit,
  onRetrySubmit,
  onForceSubmitWithUnsynced,
}: ExamSubmitModalProps) {
  if (!open && submissionState.phase !== "submitted") {
    return null;
  }

  const unansweredCount = Math.max(0, totalQuestions - answeredCount);
  const { phase } = submissionState;
  const isBusy = phase === "flushing_autosave" || phase === "submitting";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="exam-submit-modal-title"
      data-testid="exam-submit-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs"
    >
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl">
        {phase === "submitted" ? (
          <div data-testid="exam-submitted-receipt">
            <div className="inline-flex items-center rounded-full bg-teal-100 px-3 py-1 text-xs font-bold text-teal-800">
              Nộp bài thành công
            </div>
            <h2 id="exam-submit-modal-title" className="mt-3 text-lg font-bold text-slate-900">
              Hệ thống H2Edu đã ghi nhận bài thi
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              Bài làm của bạn đã được lưu an toàn và niêm phong trạng thái nộp bài trên máy chủ.
            </p>
            <dl className="mt-4 space-y-1.5 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
              {submissionState.receiptId && (
                <div className="flex justify-between gap-2">
                  <dt className="font-semibold">Mã biên nhận:</dt>
                  <dd className="font-mono">{submissionState.receiptId}</dd>
                </div>
              )}
              {submissionState.submittedAtServerIso && (
                <div className="flex justify-between gap-2">
                  <dt className="font-semibold">Thời gian máy chủ:</dt>
                  <dd className="font-mono">{submissionState.submittedAtServerIso}</dd>
                </div>
              )}
            </dl>
          </div>
        ) : phase === "rejected" && submissionState.canSubmitWithUnsynced ? (
          <div>
            <h2 id="exam-submit-modal-title" className="text-lg font-bold text-red-900">
              Có đáp án chưa thể đồng bộ lên máy chủ
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              {submissionState.errorMessage ??
                "Một số thay đổi cục bộ bị máy chủ từ chối tự động lưu. Bạn có thể nộp bài kèm danh sách câu chưa đồng bộ để hệ thống đối soát."}
            </p>
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-900">
              Số câu chưa đồng bộ: {submissionState.unsyncedAnswerIds.length} (
              {submissionState.unsyncedAnswerIds.join(", ")})
            </div>
            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700"
              >
                Quay lại
              </button>
              <button
                type="button"
                onClick={onForceSubmitWithUnsynced}
                className="rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white hover:bg-red-800"
              >
                Xác nhận nộp kèm câu chưa đồng bộ
              </button>
            </div>
          </div>
        ) : (
          <div>
            <h2 id="exam-submit-modal-title" className="text-lg font-bold text-slate-900">
              Xác nhận nộp bài thi
            </h2>
            <p className="mt-1.5 text-sm text-slate-600">
              Sau khi nộp bài, bạn sẽ không thể chỉnh sửa đáp án. Hãy kiểm tra lại thống kê dưới đây:
            </p>

            <div className="mt-4 grid grid-cols-2 gap-2.5 text-xs">
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="text-slate-500">Đã hoàn thành</div>
                <div className="mt-0.5 text-lg font-bold tabular-nums text-teal-700">
                  {answeredCount}/{totalQuestions}
                </div>
              </div>
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="text-slate-500">Chưa làm</div>
                <div className="mt-0.5 text-lg font-bold tabular-nums text-slate-800">
                  {unansweredCount}
                </div>
              </div>
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                <div className="text-amber-800">Thiếu ý Đúng/Sai</div>
                <div className="mt-0.5 text-lg font-bold tabular-nums text-amber-900">
                  {partialPart2Count}
                </div>
              </div>
              <div className="rounded-xl border border-orange-200 bg-orange-50 p-3">
                <div className="text-orange-800">Đang cắm cờ</div>
                <div className="mt-0.5 text-lg font-bold tabular-nums text-orange-900">
                  {flaggedCount}
                </div>
              </div>
            </div>

            {phase === "offline_retry_banner" && (
              <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950">
                {submissionState.errorMessage ??
                  "Đang chờ mạng ổn định để hoàn tất nộp bài (dữ liệu đã khóa an toàn)."}
              </div>
            )}

            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              {!isBusy && (
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                >
                  Tiếp tục làm bài
                </button>
              )}
              {phase === "offline_retry_banner" ? (
                <button
                  type="button"
                  onClick={onRetrySubmit}
                  className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700"
                >
                  Thử nộp lại ngay
                </button>
              ) : (
                <button
                  type="button"
                  disabled={isBusy}
                  data-testid="confirm-submit-btn"
                  onClick={onConfirmSubmit}
                  className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-800 disabled:opacity-60"
                >
                  {isBusy ? "Đang đồng bộ & nộp bài..." : "Xác nhận nộp bài"}
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
