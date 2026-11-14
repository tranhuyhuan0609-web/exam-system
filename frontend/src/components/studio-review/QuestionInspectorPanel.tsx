"use client";

import React from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import {
  evaluatePublishButtonState,
  inspectAnswerDispute,
  type ReviewerRightsStatus,
  type ReviewerVerificationStatus,
} from "@/lib/bbox-reviewer";
import type { ReviewQuestionItem } from "./types";

export interface QuestionInspectorPanelProps {
  items: readonly ReviewQuestionItem[];
  selectedItem: ReviewQuestionItem;
  actionFeedback: string | null;
  isSubmitting: boolean;
  onSelectItem: (itemId: string) => void;
  onUpdateItem: (itemId: string, patch: Partial<ReviewQuestionItem>) => void;
  onMarkVerified: (itemId: string) => void;
  onApproveItem: (itemId: string) => void;
  onPublishItem: (itemId: string) => void;
  onSaveStructure: () => void;
}

const RIGHTS_OPTIONS: readonly ReviewerRightsStatus[] = [
  "PENDING_REVIEW",
  "OWNED",
  "LICENSED",
  "ALLOWED_DERIVATIVE",
  "RESTRICTED",
  "TAKEDOWN",
];

const VERIFICATION_OPTIONS: readonly ReviewerVerificationStatus[] = [
  "UNCHECKED",
  "AUTO_VERIFIED",
  "VERIFIED",
  "DISPUTED",
  "REJECTED",
];

export function QuestionInspectorPanel({
  items,
  selectedItem,
  actionFeedback,
  isSubmitting,
  onSelectItem,
  onUpdateItem,
  onMarkVerified,
  onApproveItem,
  onPublishItem,
  onSaveStructure,
}: QuestionInspectorPanelProps) {
  const dispute = inspectAnswerDispute(selectedItem.sourceAnswer, selectedItem.sympyAnswer);
  const publishState = evaluatePublishButtonState({
    rightsStatus: selectedItem.rightsStatus,
    verificationStatus: selectedItem.verificationStatus,
    isDisputed: dispute.status === "DISPUTED_ANSWER" || selectedItem.verificationStatus === "DISPUTED",
  });

  return (
    <section
      data-testid="ws3-right-inspector-pane"
      className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
    >
      {/* Question Selector Bar */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">
            Danh sách câu hỏi cần duyệt ({items.length})
          </h2>
          <button
            type="button"
            data-testid="ws3-save-structure-btn"
            disabled={isSubmitting}
            onClick={onSaveStructure}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
          >
            Lưu thay đổi &amp; BBox
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          {items.map((item) => {
            const itemDispute = inspectAnswerDispute(item.sourceAnswer, item.sympyAnswer);
            const active = item.id === selectedItem.id;
            return (
              <button
                key={item.id}
                type="button"
                data-testid={`ws3-select-item-${item.id}`}
                onClick={() => onSelectItem(item.id)}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                  active
                    ? "border-indigo-600 bg-indigo-50 text-indigo-900 ring-1 ring-indigo-600"
                    : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                <span>Câu {item.orderIndex}</span>
                {itemDispute.status === "DISPUTED_ANSWER" ? (
                  <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-bold text-red-700">
                    DISPUTED
                  </span>
                ) : item.verificationStatus === "VERIFIED" ? (
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700">
                    VERIFIED
                  </span>
                ) : (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800">
                    {item.verificationStatus}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Disputed Answer / Matched SymPy Banner */}
      {dispute.status === "DISPUTED_ANSWER" ? (
        <div
          role="alert"
          data-testid="ws3-disputed-answer-alert"
          className="rounded-xl border border-red-300 bg-red-50 p-3.5 text-sm text-red-900"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span
              data-testid="ws3-disputed-answer-badge"
              className="rounded-md bg-red-700 px-2 py-0.5 text-xs font-extrabold text-white"
            >
              DISPUTED_ANSWER
            </span>
            <span className="font-bold">Đáp án nguồn (HDC) mâu thuẫn với nghiệm độc lập của SymPy!</span>
          </div>
          <p className="mt-1.5 text-xs text-red-800">
            HDC: <code>{dispute.normalizedSource}</code> ≠ SymPy: <code>{dispute.normalizedSympy}</code>.{" "}
            {selectedItem.sympyTraceSummary}
          </p>
        </div>
      ) : dispute.status === "MATCHED" ? (
        <div
          data-testid="ws3-sympy-matched-alert"
          className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900"
        >
          <span className="mr-2 rounded bg-emerald-700 px-1.5 py-0.5 font-bold text-white">SYMPY MATCHED</span>
          {selectedItem.sympyTraceSummary}
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
          Chưa đủ dữ liệu đối chiếu tự động từ SymPy.
        </div>
      )}

      {/* Stem Editor & Safe KaTeX Live Preview */}
      <div className="space-y-2">
        <label htmlFor="ws3-stem-textarea" className="block text-xs font-bold uppercase text-slate-600">
          Nội dung câu hỏi ({selectedItem.stableCode} · {selectedItem.questionType})
        </label>
        <textarea
          id="ws3-stem-textarea"
          data-testid="ws3-stem-input"
          rows={3}
          value={selectedItem.stemMarkdown}
          onChange={(e) => onUpdateItem(selectedItem.id, { stemMarkdown: e.target.value })}
          className="w-full rounded-xl border border-slate-300 p-3 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:outline-none"
        />
        <div
          data-testid="ws3-stem-katex-preview"
          className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed text-slate-800"
        >
          <SafeKatexText text={selectedItem.stemMarkdown} />
        </div>
      </div>

      {/* Source Answer vs SymPy Comparison */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="ws3-source-answer" className="mb-1 block text-xs font-bold text-slate-700">
            Đáp án nguồn (HDC)
          </label>
          <input
            id="ws3-source-answer"
            data-testid="ws3-source-answer-input"
            type="text"
            value={selectedItem.sourceAnswer}
            onChange={(e) => onUpdateItem(selectedItem.id, { sourceAnswer: e.target.value })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-900 focus:border-indigo-500 focus:outline-none"
          />
        </div>
        <div>
          <span className="mb-1 block text-xs font-bold text-slate-700">Đáp án độc lập SymPy + LLM</span>
          <div
            data-testid="ws3-sympy-answer-value"
            className="rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 font-mono text-sm font-bold text-slate-900"
          >
            {selectedItem.sympyAnswer || "—"}
          </div>
        </div>
      </div>

      {/* Governance Axes: rights_status & verification_status */}
      <div className="grid grid-cols-1 gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3.5 sm:grid-cols-2">
        <div>
          <label htmlFor="ws3-rights-select" className="mb-1 block text-xs font-bold text-slate-700">
            Trạng thái bản quyền (rights_status)
          </label>
          <select
            id="ws3-rights-select"
            data-testid="ws3-rights-select"
            value={selectedItem.rightsStatus}
            onChange={(e) =>
              onUpdateItem(selectedItem.id, { rightsStatus: e.target.value as ReviewerRightsStatus })
            }
            className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs font-semibold text-slate-900"
          >
            {RIGHTS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="ws3-verification-select" className="mb-1 block text-xs font-bold text-slate-700">
            Kiểm định chuyên môn (verification_status)
          </label>
          <select
            id="ws3-verification-select"
            data-testid="ws3-verification-select"
            value={selectedItem.verificationStatus}
            onChange={(e) =>
              onUpdateItem(selectedItem.id, {
                verificationStatus: e.target.value as ReviewerVerificationStatus,
              })
            }
            className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs font-semibold text-slate-900"
          >
            {VERIFICATION_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Publish Lock Gate Explanation + Action Buttons */}
      <div className="space-y-3 border-t border-slate-200 pt-3">
        {publishState.disabled && (
          <div
            data-testid="ws3-publish-reasons"
            className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950"
          >
            <p className="font-bold">Cổng an toàn đang khóa Publish / Approve:</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {publishState.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            data-testid="ws3-mark-verified-btn"
            disabled={isSubmitting || dispute.status === "DISPUTED_ANSWER"}
            onClick={() => onMarkVerified(selectedItem.id)}
            className="rounded-lg border border-emerald-600 bg-emerald-50 px-3.5 py-2 text-xs font-bold text-emerald-900 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Xác nhận VERIFIED
          </button>

          <button
            type="button"
            data-testid="ws3-approve-btn"
            disabled={isSubmitting || publishState.disabled}
            onClick={() => onApproveItem(selectedItem.id)}
            className="rounded-lg bg-blue-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
          >
            Approve (Cấu trúc &amp; Ứng viên)
          </button>

          <button
            type="button"
            data-testid="ws3-publish-btn"
            disabled={isSubmitting || publishState.disabled}
            onClick={() => onPublishItem(selectedItem.id)}
            className="rounded-lg bg-indigo-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-indigo-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
          >
            Publish lên Kho
          </button>
        </div>

        {actionFeedback && (
          <p data-testid="ws3-action-feedback" className="text-xs font-semibold text-indigo-800">
            {actionFeedback}
          </p>
        )}
      </div>
    </section>
  );
}
