"use client";

import React, { useMemo, useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import { evaluateClusterTrueFalseState, validateShortNumericInput } from "@/lib/exam-input-policy";
import { buildInteractivePracticeTabs, type PracticeTabType } from "@/lib/workspace1-study-hub";
import type { StudyBlock, StudyPracticeItem } from "./types";

const BLOCK_STYLES: Record<
  StudyBlock["kind"],
  { readonly wrapper: string; readonly badge: string; readonly label: string }
> = {
  THEORY_SUMMARY: {
    wrapper: "border-slate-200 bg-white",
    badge: "bg-slate-100 text-slate-700",
    label: "Tóm tắt lý thuyết",
  },
  FORMULA_BOX: {
    wrapper: "border-blue-300 bg-blue-50/70 ring-1 ring-blue-100",
    badge: "bg-blue-700 text-white",
    label: "Hộp công thức",
  },
  METHOD_STEPS: {
    wrapper: "border-indigo-200 bg-indigo-50/40",
    badge: "bg-indigo-100 text-indigo-800",
    label: "Các bước giải",
  },
  TIP_WARNING: {
    wrapper: "border-amber-300 bg-amber-50/70",
    badge: "bg-amber-100 text-amber-900",
    label: "Chú ý / Bẫy hay sai",
  },
  WORKED_EXAMPLE: {
    wrapper: "border-emerald-200 bg-white",
    badge: "bg-emerald-100 text-emerald-800",
    label: "Ví dụ mẫu",
  },
};

export interface LessonPracticeColumnProps {
  title: string;
  subjectLabel: string;
  gradeLabel: string;
  bookSeries: string;
  blocks: readonly StudyBlock[];
  practiceItems: readonly StudyPracticeItem[];
  onRecordIncorrect?: (item: StudyPracticeItem) => void;
}

export function LessonPracticeColumn({
  title,
  subjectLabel,
  gradeLabel,
  bookSeries,
  blocks,
  practiceItems,
  onRecordIncorrect,
}: LessonPracticeColumnProps) {
  const [expandedSolutions, setExpandedSolutions] = useState<Record<string, boolean>>({});
  const [activeTab, setActiveTab] = useState<PracticeTabType>("SINGLE_CHOICE");
  const [revealedHints, setRevealedHints] = useState<Record<string, boolean>>({});
  const [checkedItems, setCheckedItems] = useState<Record<string, { correct: boolean; message: string }>>({});

  // Practice answer drafts keyed by item.id
  const [singleDrafts, setSingleDrafts] = useState<Record<string, string>>({});
  const [clusterDrafts, setClusterDrafts] = useState<
    Record<string, Partial<Record<"a" | "b" | "c" | "d", boolean | null>>>
  >({});
  const [shortDrafts, setShortDrafts] = useState<Record<string, string>>({});
  const [essayDrafts, setEssayDrafts] = useState<Record<string, string>>({});

  const tabs = useMemo(() => {
    const counts: Partial<Record<PracticeTabType, number>> = {
      ESSAY: 0,
      SINGLE_CHOICE: 0,
      CLUSTER_TRUE_FALSE: 0,
      SHORT_NUMERIC: 0,
    };
    for (const item of practiceItems) {
      counts[item.tabType] = (counts[item.tabType] ?? 0) + 1;
    }
    return buildInteractivePracticeTabs(counts);
  }, [practiceItems]);

  const activeItems = useMemo(
    () => practiceItems.filter((item) => item.tabType === activeTab),
    [practiceItems, activeTab],
  );

  const toggleSolution = (blockId: string) => {
    setExpandedSolutions((prev) => ({ ...prev, [blockId]: !prev[blockId] }));
  };

  const handleGradePracticeItem = (item: StudyPracticeItem) => {
    const expected = item.expectedAnswer;
    if (!expected) {
      setCheckedItems((prev) => ({
        ...prev,
        [item.id]: { correct: true, message: "Đã ghi nhận bài làm để tự đối chiếu." },
      }));
      return;
    }

    if (expected.kind === "SINGLE_CHOICE") {
      const chosen = singleDrafts[item.id];
      const correct = chosen === expected.key;
      if (!correct) {
        onRecordIncorrect?.(item);
      }
      setCheckedItems((prev) => ({
        ...prev,
        [item.id]: {
          correct,
          message: correct
            ? "Chính xác! Lời giải chi tiết được mở ngay bên dưới."
            : `Chưa chính xác (bạn chọn ${chosen ?? "trống"}, đáp án đúng là ${expected.key}). Đã thêm vào Sổ tay câu sai.`,
        },
      }));
      return;
    }

    if (expected.kind === "CLUSTER_TRUE_FALSE") {
      const draft = clusterDrafts[item.id] ?? {};
      const state = evaluateClusterTrueFalseState(draft);
      if (state !== "COMPLETE") {
        setCheckedItems((prev) => ({
          ...prev,
          [item.id]: {
            correct: false,
            message: "Vui lòng chọn đủ cả 4 ý a, b, c, d trước khi kiểm tra.",
          },
        }));
        return;
      }
      const correct = (["a", "b", "c", "d"] as const).every((k) => draft[k] === expected.map[k]);
      if (!correct) {
        onRecordIncorrect?.(item);
      }
      setCheckedItems((prev) => ({
        ...prev,
        [item.id]: {
          correct,
          message: correct
            ? "Chính xác cả 4/4 ý Đúng/Sai!"
            : "Có ý chưa khớp với đáp án chuẩn — xem giải thích từng ý bên dưới.",
        },
      }));
      return;
    }

    if (expected.kind === "SHORT_NUMERIC") {
      const raw = shortDrafts[item.id] ?? "";
      const validation = validateShortNumericInput(raw);
      const parsedNumber =
        validation.normalizedValue !== null ? Number(validation.normalizedValue) : Number.NaN;
      if (!validation.valid || !Number.isFinite(parsedNumber)) {
        setCheckedItems((prev) => ({
          ...prev,
          [item.id]: {
            correct: false,
            message: validation.warning ?? "Vui lòng nhập giá trị số hợp lệ (tối đa 4 ký tự).",
          },
        }));
        return;
      }
      const correct = Math.abs(parsedNumber - expected.numericValue) < 1e-6;
      if (!correct) {
        onRecordIncorrect?.(item);
      }
      setCheckedItems((prev) => ({
        ...prev,
        [item.id]: {
          correct,
          message: correct
            ? `Chính xác! Giá trị quy chuẩn là ${validation.normalizedValue}.`
            : `Chưa đúng (giá trị bạn nhập là ${validation.normalizedValue}, đáp án đúng là ${expected.numericValue}).`,
        },
      }));
      return;
    }

    setCheckedItems((prev) => ({
      ...prev,
      [item.id]: {
        correct: true,
        message: "Đã lưu bài tự luận và hiển thị hướng dẫn chấm mẫu.",
      },
    }));
  };

  return (
    <section
      data-testid="workspace1-reading-column"
      className="min-w-0 w-full max-w-[760px] flex-1 space-y-6"
    >
      {/* Lesson header card */}
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
        <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-blue-700">
          <span className="rounded-full bg-blue-50 px-2.5 py-0.5">{bookSeries}</span>
          <span>·</span>
          <span>{subjectLabel}</span>
          <span>·</span>
          <span>{gradeLabel}</span>
        </div>
        <h1 className="mt-2 text-2xl font-extrabold tracking-tight text-slate-900">{title}</h1>
      </div>

      {/* 5 VietJack-style lesson blocks */}
      <div className="space-y-4">
        {blocks.map((block) => {
          const style = BLOCK_STYLES[block.kind];
          const solutionOpen = !!expandedSolutions[block.id];

          return (
            <article
              key={block.id}
              data-testid={`study-block-${block.kind}`}
              className={`rounded-2xl border p-5 shadow-xs ${style.wrapper}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${style.badge}`}>
                  {style.label}
                </span>
              </div>
              <h2 className="mt-2 text-base font-bold text-slate-900">{block.title}</h2>
              <div className="mt-2 text-sm leading-relaxed text-slate-800">
                <SafeKatexText text={block.content} />
              </div>

              {block.steps && block.steps.length > 0 && (
                <ol className="mt-3 space-y-1.5 pl-1 text-sm text-slate-800">
                  {block.steps.map((step, idx) => (
                    <li key={idx} className="rounded-lg bg-white/80 px-3 py-2 ring-1 ring-indigo-100">
                      <SafeKatexText text={step} />
                    </li>
                  ))}
                </ol>
              )}

              {block.kind === "WORKED_EXAMPLE" && block.solutionContent && (
                <div className="mt-4">
                  <button
                    type="button"
                    data-testid={`toggle-solution-${block.id}`}
                    aria-expanded={solutionOpen}
                    onClick={() => toggleSolution(block.id)}
                    className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-bold text-emerald-900 transition hover:bg-emerald-100"
                  >
                    {solutionOpen ? "Ẩn lời giải" : "Xem lời giải"}
                  </button>

                  {solutionOpen && (
                    <div
                      data-testid={`worked-solution-${block.id}`}
                      className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 text-sm text-slate-800"
                    >
                      <SafeKatexText text={block.solutionContent} />
                    </div>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>

      {/* 4 Interactive Practice Tabs */}
      <div
        data-testid="workspace1-practice-tabs"
        className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
      >
        <div className="border-b border-slate-200 pb-3">
          <h2 className="text-base font-bold text-slate-900">Bài tập thực hành 4 dạng THPTQG</h2>
          <p className="text-xs text-slate-500">
            Chấm phản hồi tức thì (IMMEDIATE_FEEDBACK), tách biệt hoàn toàn khỏi phòng thi chính thức.
          </p>

          <div role="tablist" aria-label="4 dạng bài tập tương tác" className="mt-3 flex flex-wrap gap-2">
            {tabs.map((tab) => {
              const selected = activeTab === tab.type;
              return (
                <button
                  key={tab.type}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  data-testid={`practice-tab-${tab.type}`}
                  disabled={!tab.enabled}
                  onClick={() => setActiveTab(tab.type)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-bold transition ${
                    selected
                      ? "bg-blue-700 text-white shadow-xs"
                      : tab.enabled
                        ? "border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                        : "cursor-not-allowed border border-slate-100 bg-slate-50 text-slate-400"
                  }`}
                >
                  {tab.label} ({tab.count})
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-4 space-y-4">
          {activeItems.length === 0 ? (
            <p className="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-500">
              Chưa có bài tập cho dạng này.
            </p>
          ) : (
            activeItems.map((item) => {
              const feedback = checkedItems[item.id];
              const showHint = !!revealedHints[item.id];
              const shortValue = shortDrafts[item.id] ?? "";
              const shortValidation =
                item.tabType === "SHORT_NUMERIC" && shortValue.length > 0
                  ? validateShortNumericInput(shortValue)
                  : null;
              const clusterState =
                item.tabType === "CLUSTER_TRUE_FALSE"
                  ? evaluateClusterTrueFalseState(clusterDrafts[item.id] ?? {})
                  : null;

              return (
                <div
                  key={item.id}
                  data-testid={`practice-item-${item.id}`}
                  className="rounded-xl border border-slate-200 bg-slate-50/50 p-4"
                >
                  <div className="text-xs font-bold uppercase tracking-wide text-blue-700">{item.label}</div>
                  <div className="mt-1.5 text-sm leading-relaxed text-slate-900">
                    <SafeKatexText text={item.promptLatex} />
                  </div>

                  {item.tabType === "SINGLE_CHOICE" && item.options && (
                    <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {item.options.map((opt) => {
                        const chosen = singleDrafts[item.id] === opt.key;
                        return (
                          <button
                            key={opt.key}
                            type="button"
                            data-testid={`practice-option-${item.id}-${opt.key}`}
                            onClick={() =>
                              setSingleDrafts((prev) => ({ ...prev, [item.id]: opt.key }))
                            }
                            className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition ${
                              chosen
                                ? "border-blue-600 bg-blue-50 font-semibold text-blue-900"
                                : "border-slate-200 bg-white text-slate-800 hover:bg-slate-50"
                            }`}
                          >
                            <span className="font-bold">{opt.key}.</span>
                            <SafeKatexText text={opt.label} />
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {item.tabType === "CLUSTER_TRUE_FALSE" && item.statements && (
                    <div className="mt-3 space-y-2">
                      {item.statements.map((stmt) => {
                        const val = clusterDrafts[item.id]?.[stmt.key] ?? null;
                        return (
                          <div
                            key={stmt.key}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
                          >
                            <div>
                              <span className="mr-1.5 font-bold">{stmt.key})</span>
                              <SafeKatexText text={stmt.text} />
                            </div>
                            <div className="flex gap-1.5">
                              <button
                                type="button"
                                onClick={() =>
                                  setClusterDrafts((prev) => ({
                                    ...prev,
                                    [item.id]: { ...(prev[item.id] ?? {}), [stmt.key]: true },
                                  }))
                                }
                                className={`rounded px-2.5 py-1 text-xs font-semibold ${
                                  val === true
                                    ? "bg-emerald-600 text-white"
                                    : "border border-slate-200 bg-slate-50 text-slate-700"
                                }`}
                              >
                                Đúng
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setClusterDrafts((prev) => ({
                                    ...prev,
                                    [item.id]: { ...(prev[item.id] ?? {}), [stmt.key]: false },
                                  }))
                                }
                                className={`rounded px-2.5 py-1 text-xs font-semibold ${
                                  val === false
                                    ? "bg-rose-600 text-white"
                                    : "border border-slate-200 bg-slate-50 text-slate-700"
                                }`}
                              >
                                Sai
                              </button>
                            </div>
                          </div>
                        );
                      })}
                      {clusterState === "PARTIAL_WARNING" && (
                        <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-900">
                          Cảnh báo: Bạn mới chọn một phần các ý Đúng/Sai — hãy hoàn thành đủ cả 4 ý a, b, c, d.
                        </p>
                      )}
                    </div>
                  )}

                  {item.tabType === "SHORT_NUMERIC" && (
                    <div className="mt-3 space-y-2">
                      <input
                        type="text"
                        inputMode="decimal"
                        data-testid={`short-input-${item.id}`}
                        value={shortValue}
                        onChange={(e) =>
                          setShortDrafts((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                        placeholder="Tối đa 4 ký tự (VD: 0,5)"
                        className="w-48 rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono text-sm text-slate-900 focus:border-blue-600 focus:outline-none"
                      />
                      {shortValidation && !shortValidation.valid && (
                        <p
                          role="alert"
                          data-testid={`short-error-${item.id}`}
                          className="text-xs font-semibold text-red-700"
                        >
                          {shortValidation.warning}
                        </p>
                      )}
                    </div>
                  )}

                  {item.tabType === "ESSAY" && (
                    <div className="mt-3">
                      <textarea
                        rows={3}
                        data-testid={`essay-input-${item.id}`}
                        value={essayDrafts[item.id] ?? ""}
                        onChange={(e) =>
                          setEssayDrafts((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                        placeholder="Nhập các bước giải tóm tắt của bạn..."
                        className="w-full rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-slate-900 focus:border-blue-600 focus:outline-none"
                      />
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      data-testid={`check-practice-${item.id}`}
                      onClick={() => handleGradePracticeItem(item)}
                      className="rounded-lg bg-blue-700 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-blue-800"
                    >
                      Kiểm tra đáp án
                    </button>
                    <button
                      type="button"
                      data-testid={`hint-practice-${item.id}`}
                      onClick={() =>
                        setRevealedHints((prev) => ({ ...prev, [item.id]: !prev[item.id] }))
                      }
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
                    >
                      {showHint ? "Ẩn gợi ý" : "Mở gợi ý từng bước"}
                    </button>
                  </div>

                  {showHint && (
                    <div
                      data-testid={`practice-hint-${item.id}`}
                      className="mt-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs text-indigo-950"
                    >
                      <SafeKatexText text={item.hintText} />
                    </div>
                  )}

                  {feedback && (
                    <div
                      data-testid={`practice-feedback-${item.id}`}
                      className={`mt-2 rounded-lg border px-3 py-2 text-xs ${
                        feedback.correct
                          ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                          : "border-rose-200 bg-rose-50 text-rose-900"
                      }`}
                    >
                      <div className="font-bold">{feedback.message}</div>
                      <div className="mt-1 text-slate-800">
                        <SafeKatexText text={item.explanationLatex} />
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </section>
  );
}
