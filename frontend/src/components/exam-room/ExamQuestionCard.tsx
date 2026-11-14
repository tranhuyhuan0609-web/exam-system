"use client";

import React, { useEffect, useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import {
  evaluateClusterTrueFalseState,
  MAX_SHORT_NUMERIC_CHARS,
  validateShortNumericInput,
} from "@/lib/exam-input-policy";
import type {
  ChoiceLetter,
  ClusterSubKey,
  ExamQuestion,
  QuestionAnswerState,
} from "./types";

export interface ExamQuestionCardProps {
  question: ExamQuestion;
  totalQuestions: number;
  answerState: QuestionAnswerState;
  disabled?: boolean;
  onSelectChoice: (choice: ChoiceLetter) => void;
  onToggleEliminate: (choice: ChoiceLetter) => void;
  onToggleClusterValue: (subKey: ClusterSubKey, value: boolean) => void;
  onChangeShortNumeric: (normalizedOrEmpty: string) => void;
  onToggleFlag: () => void;
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
}

const PART_LABELS: Record<ExamQuestion["part"], string> = {
  PART_1: "Phần I • Trắc nghiệm nhiều lựa chọn",
  PART_2: "Phần II • Trắc nghiệm Đúng / Sai",
  PART_3: "Phần III • Trả lời ngắn (4 ô)",
};

export function ExamQuestionCard({
  question,
  totalQuestions,
  answerState,
  disabled = false,
  onSelectChoice,
  onToggleEliminate,
  onToggleClusterValue,
  onChangeShortNumeric,
  onToggleFlag,
  onPrev,
  onNext,
  canPrev,
  canNext,
}: ExamQuestionCardProps) {
  const [shortDraft, setShortDraft] = useState<string>(answerState.shortText.replace(".", ","));
  const [shortWarning, setShortWarning] = useState<string | null>(null);
  const lastQuestionIdRef = React.useRef<string>(question.questionVersionId);

  useEffect(() => {
    const switchedQuestion = lastQuestionIdRef.current !== question.questionVersionId;
    lastQuestionIdRef.current = question.questionVersionId;
    const currentNormalized = validateShortNumericInput(shortDraft).normalizedValue ?? "";
    if (switchedQuestion || answerState.shortText !== currentNormalized) {
      const nextDisplay = answerState.shortText.replace(".", ",");
      setShortDraft(nextDisplay);
      setShortWarning(validateShortNumericInput(nextDisplay).warning);
    }
  }, [question.questionVersionId, answerState.shortText, shortDraft]);

  const handleShortInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setShortDraft(raw);
    const result = validateShortNumericInput(raw);
    setShortWarning(result.warning);
    if (result.valid) {
      onChangeShortNumeric(result.normalizedValue ?? "");
    }
  };

  const clusterStatus =
    question.part === "PART_2"
      ? evaluateClusterTrueFalseState(answerState.cluster)
      : null;

  const clusterAnsweredCount =
    question.part === "PART_2"
      ? (["a", "b", "c", "d"] as const).filter(
          (k) => typeof answerState.cluster[k] === "boolean",
        ).length
      : 0;

  const gridCells = Array.from({ length: MAX_SHORT_NUMERIC_CHARS }, (_, idx) => {
    const displaySource = shortDraft.trim();
    return displaySource[idx] ?? "";
  });

  return (
    <section
      aria-label={`Câu ${question.number}`}
      data-testid={`question-card-${question.questionVersionId}`}
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs sm:p-7"
    >
      {/* Question Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center rounded-lg bg-blue-700 px-3 py-1 text-sm font-bold text-white">
            Câu {question.number} / {totalQuestions}
          </span>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
            {PART_LABELS[question.part]}
          </span>
        </div>

        <button
          type="button"
          disabled={disabled}
          aria-pressed={answerState.flagged}
          data-testid="flag-question-btn"
          onClick={onToggleFlag}
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition disabled:opacity-50 ${
            answerState.flagged
              ? "border-orange-400 bg-orange-50 text-orange-800"
              : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
          }`}
        >
          <span aria-hidden="true">{answerState.flagged ? "⚑" : "⚐"}</span>
          <span>{answerState.flagged ? "Đang cắm cờ (F)" : "Cắm cờ xem lại (F)"}</span>
        </button>
      </div>

      {/* Stem */}
      <div className="mt-5 text-base leading-relaxed text-slate-900">
        <SafeKatexText text={question.stem} />
      </div>

      {/* Part I: Multiple Choice with Strike-through Elimination & Keyboard Shortcuts */}
      {question.part === "PART_1" && (
        <div className="mt-6 space-y-3" role="radiogroup" aria-label={`Các phương án câu ${question.number}`}>
          {question.options.map((opt) => {
            const isSelected = answerState.choice === opt.key;
            const isEliminated = answerState.eliminated.includes(opt.key);
            return (
              <div
                key={opt.key}
                data-testid={`part1-option-${opt.key}`}
                data-eliminated={isEliminated ? "true" : "false"}
                className={`flex items-center justify-between gap-3 rounded-xl border p-3.5 transition ${
                  isSelected
                    ? "border-blue-600 bg-blue-50/70 ring-1 ring-blue-600"
                    : isEliminated
                      ? "border-slate-200 bg-slate-50/80 opacity-75"
                      : "border-slate-200 bg-white hover:border-slate-300"
                }`}
              >
                <button
                  type="button"
                  role="radio"
                  aria-checked={isSelected}
                  disabled={disabled}
                  onClick={() => onSelectChoice(opt.key)}
                  className="flex flex-1 items-start gap-3 text-left disabled:cursor-not-allowed"
                >
                  <span
                    className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm font-bold ${
                      isSelected
                        ? "border-blue-700 bg-blue-700 text-white"
                        : "border-slate-300 bg-white text-slate-700"
                    }`}
                  >
                    {opt.key}
                  </span>
                  <span
                    className={`pt-0.5 text-sm leading-relaxed text-slate-900 ${
                      isEliminated ? "text-slate-400 line-through" : ""
                    }`}
                  >
                    <SafeKatexText text={opt.text} />
                  </span>
                </button>

                <button
                  type="button"
                  disabled={disabled}
                  aria-pressed={isEliminated}
                  aria-label={`Loại trừ đáp án ${opt.key}`}
                  data-testid={`eliminate-${opt.key}`}
                  onClick={() => onToggleEliminate(opt.key)}
                  className={`shrink-0 rounded-md border px-2.5 py-1 text-xs font-medium transition disabled:opacity-50 ${
                    isEliminated
                      ? "border-slate-400 bg-slate-200 text-slate-800"
                      : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {isEliminated ? "Bỏ loại trừ" : "Loại trừ"}
                </button>
              </div>
            );
          })}
          <p className="pt-1 text-xs text-slate-500">
            Mẹo phím tắt: nhấn <kbd className="rounded border border-slate-300 bg-slate-100 px-1">A</kbd>–
            <kbd className="rounded border border-slate-300 bg-slate-100 px-1">D</kbd> để chọn nhanh,{" "}
            <kbd className="rounded border border-slate-300 bg-slate-100 px-1">←</kbd>/
            <kbd className="rounded border border-slate-300 bg-slate-100 px-1">→</kbd> chuyển câu.
          </p>
        </div>
      )}

      {/* Part II: True/False 4 Sub-items (a, b, c, d) with Partial Warning */}
      {question.part === "PART_2" && (
        <div className="mt-6 space-y-3">
          {clusterStatus === "PARTIAL_WARNING" && (
            <div
              role="status"
              data-testid="part2-partial-warning"
              className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950"
            >
              <span className="font-semibold">Cảnh báo chưa hoàn thành đủ 4 ý:</span> Bạn mới chọn{" "}
              {clusterAnsweredCount}/4 mệnh đề (a, b, c, d). Hãy kiểm tra lại để không bỏ sót điểm thành phần!
            </div>
          )}

          {question.statements.map((stmt) => {
            const currentVal = answerState.cluster[stmt.key];
            return (
              <div
                key={stmt.key}
                data-testid={`part2-row-${stmt.key}`}
                className="flex flex-col justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center"
              >
                <div className="flex items-start gap-2.5 text-sm leading-relaxed text-slate-900">
                  <span className="font-bold text-blue-800">{stmt.key})</span>
                  <SafeKatexText text={stmt.text} />
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    disabled={disabled}
                    aria-pressed={currentVal === true}
                    data-testid={`tf-${stmt.key}-true`}
                    onClick={() => onToggleClusterValue(stmt.key, true)}
                    className={`rounded-lg border px-4 py-1.5 text-xs font-bold transition disabled:opacity-50 ${
                      currentVal === true
                        ? "border-teal-700 bg-teal-700 text-white"
                        : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    Đúng
                  </button>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-pressed={currentVal === false}
                    data-testid={`tf-${stmt.key}-false`}
                    onClick={() => onToggleClusterValue(stmt.key, false)}
                    className={`rounded-lg border px-4 py-1.5 text-xs font-bold transition disabled:opacity-50 ${
                      currentVal === false
                        ? "border-rose-700 bg-rose-700 text-white"
                        : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    Sai
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Part III: THPTQG 4-Cell Short Numeric Input */}
      {question.part === "PART_3" && (
        <div className="mt-6 space-y-4 rounded-xl border border-slate-200 bg-slate-50/70 p-5">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-600">
              Phiếu tô 4 ô trả lời ngắn (tối đa {MAX_SHORT_NUMERIC_CHARS} ký tự)
            </div>
            <div
              data-testid="part3-four-cell-grid"
              className="mt-2.5 inline-flex items-center gap-2"
              aria-hidden="true"
            >
              {gridCells.map((ch, idx) => (
                <div
                  key={idx}
                  data-testid={`part3-cell-${idx}`}
                  className={`flex h-12 w-11 items-center justify-center rounded-lg border-2 font-mono text-lg font-bold tabular-nums ${
                    shortWarning
                      ? "border-red-400 bg-red-50 text-red-800"
                      : ch
                        ? "border-blue-600 bg-white text-slate-900"
                        : "border-slate-300 bg-white text-slate-400"
                  }`}
                >
                  {ch || "•"}
                </div>
              ))}
            </div>
          </div>

          <div>
            <label
              htmlFor={`short-input-${question.questionVersionId}`}
              className="block text-sm font-semibold text-slate-800"
            >
              Nhập giá trị số
            </label>
            <input
              id={`short-input-${question.questionVersionId}`}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              disabled={disabled}
              value={shortDraft}
              onChange={handleShortInputChange}
              placeholder="Ví dụ: 1024, -2,5 hoặc 0,75"
              data-testid="part3-short-input"
              className={`mt-1.5 w-full max-w-xs rounded-lg border px-3.5 py-2.5 font-mono text-base font-semibold tabular-nums outline-none transition disabled:opacity-50 ${
                shortWarning
                  ? "border-red-500 bg-red-50/60 text-red-900 focus:ring-2 focus:ring-red-300"
                  : "border-slate-300 bg-white text-slate-900 focus:border-blue-600 focus:ring-2 focus:ring-blue-200"
              }`}
            />
            {shortWarning ? (
              <p
                role="alert"
                data-testid="part3-validation-warning"
                className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-800"
              >
                {shortWarning}
              </p>
            ) : (
              <p className="mt-1.5 text-xs text-slate-600">
                Chỉ nhập chữ số <code className="font-mono">0-9</code>, dấu{" "}
                <code className="font-mono">-</code> ở đầu và tối đa 1 dấu thập phân{" "}
                <code className="font-mono">,</code> (không dùng dấu chấm hàng nghìn như{" "}
                <code className="font-mono">1.000</code>).
              </p>
            )}
          </div>
        </div>
      )}

      {/* Navigation Footer */}
      <div className="mt-8 flex items-center justify-between border-t border-slate-100 pt-4">
        <button
          type="button"
          disabled={!canPrev}
          onClick={onPrev}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          ← Câu trước
        </button>
        <button
          type="button"
          disabled={!canNext}
          onClick={onNext}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Câu tiếp theo →
        </button>
      </div>
    </section>
  );
}
