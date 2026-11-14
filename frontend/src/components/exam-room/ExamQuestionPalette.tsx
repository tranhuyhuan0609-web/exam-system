"use client";

import React, { useState } from "react";
import { evaluateClusterTrueFalseState } from "@/lib/exam-input-policy";
import {
  type AnswerMap,
  type ExamQuestion,
  isPart1Answered,
  isPart3Answered,
} from "./types";

export interface ExamQuestionPaletteProps {
  questions: readonly ExamQuestion[];
  answers: AnswerMap;
  activeQuestionIndex: number;
  onSelectQuestion: (index: number) => void;
}

export type PaletteItemStatus = "unanswered" | "answered" | "partial" | "complete";

export function getPaletteItemStatus(
  question: ExamQuestion,
  answers: AnswerMap,
): PaletteItemStatus {
  const ans = answers[question.questionVersionId];
  if (question.part === "PART_1") {
    return isPart1Answered(ans) ? "answered" : "unanswered";
  }
  if (question.part === "PART_2") {
    const clusterState = evaluateClusterTrueFalseState(ans?.cluster ?? {});
    if (clusterState === "COMPLETE") return "complete";
    if (clusterState === "PARTIAL_WARNING") return "partial";
    return "unanswered";
  }
  return isPart3Answered(ans) ? "answered" : "unanswered";
}

type PaletteFilter = "all" | "attention" | "flagged";

export function ExamQuestionPalette({
  questions,
  answers,
  activeQuestionIndex,
  onSelectQuestion,
}: ExamQuestionPaletteProps) {
  const [filter, setFilter] = useState<PaletteFilter>("all");

  let completedCount = 0;
  let partialCount = 0;
  let flaggedCount = 0;

  const statuses = questions.map((q) => {
    const status = getPaletteItemStatus(q, answers);
    const flagged = Boolean(answers[q.questionVersionId]?.flagged);
    if (status === "answered" || status === "complete") completedCount += 1;
    if (status === "partial") partialCount += 1;
    if (flagged) flaggedCount += 1;
    return { q, status, flagged };
  });

  const parts: Array<{ part: ExamQuestion["part"]; heading: string }> = [
    { part: "PART_1", heading: "Phần I (Trắc nghiệm)" },
    { part: "PART_2", heading: "Phần II (Đúng / Sai)" },
    { part: "PART_3", heading: "Phần III (Trả lời ngắn)" },
  ];

  return (
    <aside
      aria-label="Bảng câu hỏi phòng thi"
      data-testid="exam-question-palette"
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-bold text-slate-900">Bảng câu hỏi</h2>
        <span className="text-xs font-semibold tabular-nums text-slate-600">
          {completedCount}/{questions.length} hoàn thành
        </span>
      </div>

      {/* Quick Filter */}
      <div className="mt-3 grid grid-cols-3 gap-1.5 rounded-lg bg-slate-100 p-1 text-xs font-medium">
        <button
          type="button"
          onClick={() => setFilter("all")}
          className={`rounded-md py-1 transition ${
            filter === "all" ? "bg-white font-semibold text-slate-900 shadow-2xs" : "text-slate-600"
          }`}
        >
          Tất cả
        </button>
        <button
          type="button"
          onClick={() => setFilter("attention")}
          className={`rounded-md py-1 transition ${
            filter === "attention" ? "bg-white font-semibold text-slate-900 shadow-2xs" : "text-slate-600"
          }`}
        >
          Cần rà ({questions.length - completedCount})
        </button>
        <button
          type="button"
          onClick={() => setFilter("flagged")}
          className={`rounded-md py-1 transition ${
            filter === "flagged" ? "bg-white font-semibold text-slate-900 shadow-2xs" : "text-slate-600"
          }`}
        >
          Cờ ({flaggedCount})
        </button>
      </div>

      {/* Grouped Question Buttons */}
      <div className="mt-4 space-y-4">
        {parts.map(({ part, heading }) => {
          const items = statuses
            .map((entry, idx) => ({ ...entry, idx }))
            .filter((entry) => entry.q.part === part)
            .filter((entry) => {
              if (filter === "flagged") return entry.flagged;
              if (filter === "attention") {
                return entry.status === "unanswered" || entry.status === "partial";
              }
              return true;
            });

          if (items.length === 0) return null;

          return (
            <div key={part}>
              <div className="mb-2 text-xs font-semibold text-slate-500">{heading}</div>
              <div className="grid grid-cols-5 gap-2">
                {items.map(({ q, status, flagged, idx }) => {
                  const isActive = idx === activeQuestionIndex;

                  let styleClass =
                    "border-slate-300 bg-white text-slate-700 hover:bg-slate-50";
                  if (status === "answered" || status === "complete") {
                    styleClass = "border-teal-700 bg-teal-600 text-white hover:bg-teal-700";
                  } else if (status === "partial") {
                    styleClass =
                      "border-amber-500 bg-amber-100 text-amber-950 hover:bg-amber-200";
                  }

                  return (
                    <button
                      key={q.questionVersionId}
                      type="button"
                      onClick={() => onSelectQuestion(idx)}
                      data-testid={`palette-btn-${q.number}`}
                      data-status={status}
                      data-flagged={flagged ? "true" : "false"}
                      aria-current={isActive ? "true" : undefined}
                      aria-label={`Câu ${q.number}${status === "partial" ? " thiếu ý Đúng/Sai" : ""}${flagged ? " đang cắm cờ" : ""}`}
                      className={`relative flex h-10 items-center justify-center rounded-lg border text-xs font-bold tabular-nums transition ${styleClass} ${
                        isActive ? "ring-2 ring-blue-600 ring-offset-1" : ""
                      }`}
                    >
                      <span>{q.number}</span>
                      {status === "partial" && (
                        <span
                          aria-hidden="true"
                          className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-amber-600 text-[10px] font-extrabold text-white"
                        >
                          !
                        </span>
                      )}
                      {flagged && (
                        <span
                          aria-hidden="true"
                          data-testid={`palette-flag-badge-${q.number}`}
                          className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-orange-500 text-[9px] text-white"
                        >
                          ⚑
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-5 grid grid-cols-2 gap-2 border-t border-slate-100 pt-4 text-[11px] text-slate-600">
        <div className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded bg-teal-600" />
          <span>Đã hoàn thành</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border border-slate-300 bg-white" />
          <span>Chưa làm</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded border border-amber-500 bg-amber-100" />
          <span>Thiếu ý Đ/S ({partialCount})</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-full bg-orange-500" />
          <span>Đang cắm cờ ({flaggedCount})</span>
        </div>
      </div>
    </aside>
  );
}
