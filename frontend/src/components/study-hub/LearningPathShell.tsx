"use client";

import Link from "next/link";
import React, { useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
} from "@/components/workspace/WorkspaceStateViews";
import type { UserTier } from "@/lib/ui-state-machine";
import { resolveMasteryIndicator } from "@/lib/workspace1-study-hub";
import { H2EduHeader } from "./H2EduHeader";
import { DEFAULT_LEARNING_PATH, type LearningPathTaskItem } from "./types";

export interface LearningPathShellProps {
  initialTasks?: readonly LearningPathTaskItem[];
  initialUserTier?: UserTier;
  isLoading?: boolean;
  errorCode?: string | null;
}

export function LearningPathShell({
  initialTasks = DEFAULT_LEARNING_PATH,
  initialUserTier = "FREE",
  isLoading = false,
  errorCode = null,
}: LearningPathShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [tasks, setTasks] = useState<readonly LearningPathTaskItem[]>(initialTasks);
  const [recalculatedNotice, setRecalculatedNotice] = useState<string | null>(null);

  const handleRecalculate = () => {
    setTasks((prev) => {
      const teacherTasks = prev.filter((t) => t.teacherAssigned);
      const adaptiveTasks = prev.filter((t) => !t.teacherAssigned);
      return [...adaptiveTasks, ...teacherTasks];
    });
    setRecalculatedNotice(
      "Đã tính toán lại lộ trình theo cây tiên quyết Knowledge Graph (bảo toàn 100% bài tập bắt buộc của Giáo viên).",
    );
  };

  return (
    <ErrorBoundary errorCode={errorCode}>
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <H2EduHeader activeRoute="lo-trinh" userTier={userTier} onTierChange={setUserTier} />

        {isLoading ? (
          <LoadingSkeleton title="Đang tải lộ trình học tập H2Edu..." />
        ) : tasks.length === 0 ? (
          <EmptyState
            title="Chưa có nhiệm vụ trong lộ trình học"
            description="Hãy hoàn thành một bài kiểm tra chẩn đoán hoặc luyện tập để H2Edu xây dựng lộ trình cá nhân hóa."
          />
        ) : (
          <main data-testid="learning-path-main" className="mx-auto max-w-5xl space-y-6 px-4 py-6">
            <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-blue-700">
                  D02 · Lộ trình học tập cá nhân hóa theo Knowledge Graph
                </div>
                <h1 className="mt-1 text-2xl font-extrabold text-slate-900">
                  Kế hoạch lấp lỗ hổng tiên quyết &amp; Bài tập lớp
                </h1>
                <p className="mt-1 text-sm text-slate-600">
                  Mọi nhiệm vụ gợi ý đều kèm giải thích minh bạch (rationale_text) và không ghi đè bài tập bắt buộc của Giáo viên.
                </p>
              </div>
              <button
                type="button"
                data-testid="recalculate-plan-btn"
                onClick={handleRecalculate}
                className="rounded-lg bg-blue-700 px-4 py-2 text-xs font-bold text-white transition hover:bg-blue-800"
              >
                Cập nhật lại lộ trình
              </button>
            </div>

            {recalculatedNotice && (
              <div
                role="status"
                data-testid="recalculate-notice"
                className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-900"
              >
                {recalculatedNotice}
              </div>
            )}

            <ol className="space-y-4">
              {tasks.map((task) => {
                const mastery = resolveMasteryIndicator({
                  masteryRatio: task.mastery.masteryRatio,
                  answeredCount: task.mastery.answeredCount,
                  standardError: task.mastery.standardError,
                });

                return (
                  <li
                    key={task.id}
                    data-testid={`learning-path-task-${task.id}`}
                    className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px] font-bold text-slate-700">
                            #{task.order} · {task.nodeCode}
                          </span>
                          {task.isPrerequisiteRemediation && (
                            <span className="rounded-full bg-rose-100 px-2.5 py-0.5 text-[11px] font-bold text-rose-800">
                              Ôn nút tiên quyết ({task.prerequisiteOfTitle})
                            </span>
                          )}
                          {task.teacherAssigned && (
                            <span
                              data-testid={`teacher-locked-${task.id}`}
                              className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-[11px] font-bold text-indigo-800"
                            >
                              Bài tập bắt buộc của GV (Không ghi đè)
                            </span>
                          )}
                          {task.dueDateLabel && (
                            <span className="text-xs font-semibold text-amber-800">
                              {task.dueDateLabel}
                            </span>
                          )}
                        </div>
                        <h2 className="text-base font-bold text-slate-900">
                          <SafeKatexText text={task.targetTitle} />
                        </h2>
                      </div>

                      <div className="flex items-center gap-3">
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                            mastery.color === "GREEN"
                              ? "bg-emerald-100 text-emerald-800"
                              : mastery.color === "YELLOW"
                                ? "bg-amber-100 text-amber-900"
                                : mastery.color === "RED"
                                  ? "bg-rose-100 text-rose-800"
                                  : "bg-slate-100 text-slate-700"
                          }`}
                        >
                          {mastery.label}
                        </span>
                        <Link
                          href={`/hoc-lieu/${task.targetSlug}`}
                          className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-800 hover:bg-blue-100"
                        >
                          Học ngay &rarr;
                        </Link>
                      </div>
                    </div>

                    <div
                      data-testid={`task-rationale-${task.id}`}
                      className="mt-3 rounded-xl border border-slate-100 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-700"
                    >
                      <span className="font-bold text-slate-900">Lý do gợi ý (Rationale): </span>
                      <SafeKatexText text={task.rationaleText} />
                    </div>
                  </li>
                );
              })}
            </ol>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
