"use client";

import Link from "next/link";
import React, { useState } from "react";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import type { AiTutorMode, StudyHubErrorNoteItem } from "./types";

const TUTOR_MODE_OPTIONS: readonly {
  readonly mode: AiTutorMode;
  readonly label: string;
  readonly description: string;
}[] = [
  {
    mode: "HINT_MODE",
    label: "Gợi ý bước tiếp theo",
    description: "Chỉ gợi mở công thức và bước đầu tiên, tuyệt đối không lộ đáp số.",
  },
  {
    mode: "EXPLAIN_MODE",
    label: "Giải thích chi tiết",
    description: "Phân tích từng bước biến đổi và đối chiếu nguồn đã công bố.",
  },
  {
    mode: "CHALLENGE_MODE",
    label: "Thử thách câu tương tự",
    description: "Sinh bài biến đổi số liệu cùng dạng để kiểm tra hiểu sâu.",
  },
];

interface TutorMessage {
  readonly id: string;
  readonly sender: "AI" | "LEARNER";
  readonly mode: AiTutorMode;
  readonly text: string;
}

function buildTutorReply(mode: AiTutorMode, questionText: string): string {
  const prompt = questionText.trim() || "Bài toán Bayes";
  if (mode === "HINT_MODE") {
    return `Gợi ý (HINT_MODE) cho "${prompt}": Hãy vẽ sơ đồ cây 2 tầng, xác định $P(A)$ và $P(\\overline{A})$ sao cho tổng bằng $1$, rồi viết công thức mẫu số $P(B) = P(A)P(B \\mid A) + P(\\overline{A})P(B \\mid \\overline{A})$. Tự tính tiếp bước rút gọn cuối cùng nhé!`;
  }
  if (mode === "EXPLAIN_MODE") {
    return `Giải thích chi tiết (EXPLAIN_MODE) từ học liệu đã xuất bản cho "${prompt}": Ta áp dụng $P(A \\mid B) = \\frac{P(A \\cap B)}{P(B)}$ với $P(A \\cap B) = P(A) \\cdot P(B \\mid A)$. Lưu ý đổi chiều điều kiện giữa $P(B \\mid A)$ và $P(A \\mid B)$.`;
  }
  return `Thử thách (CHALLENGE_MODE): Cho $P(A) = 0{,}15$, $P(B \\mid A) = 0{,}88$, $P(B \\mid \\overline{A}) = 0{,}04$. Hãy tính xác suất $P(A \\mid B)$ theo 4 bước chuẩn!`;
}

export interface AiTutorErrorDrawerProps {
  errorNotes: readonly StudyHubErrorNoteItem[];
  activeExamAttemptId?: string | null;
}

export function AiTutorErrorDrawer({
  errorNotes,
  activeExamAttemptId = null,
}: AiTutorErrorDrawerProps) {
  const [mode, setMode] = useState<AiTutorMode>("HINT_MODE");
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<readonly TutorMessage[]>([
    {
      id: "tutor-init-1",
      sender: "AI",
      mode: "HINT_MODE",
      text: "Xin chào! Chọn một trong 3 chế độ bên trên để Gia sư AI H2Edu hỗ trợ bạn theo nguồn học liệu đã kiểm chứng.",
    },
  ]);

  const examLocked = Boolean(activeExamAttemptId);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (examLocked || !draft.trim()) {
      return;
    }
    const learnerMsg: TutorMessage = {
      id: `user-${Date.now()}`,
      sender: "LEARNER",
      mode,
      text: draft.trim(),
    };
    const replyMsg: TutorMessage = {
      id: `ai-${Date.now() + 1}`,
      sender: "AI",
      mode,
      text: buildTutorReply(mode, draft),
    };
    setMessages((prev) => [...prev, learnerMsg, replyMsg]);
    setDraft("");
  };

  return (
    <aside
      data-testid="workspace1-tutor-drawer"
      aria-label="Gia sư AI và Sổ tay câu sai"
      className="w-full shrink-0 lg:w-[320px]"
    >
      <div className="sticky top-[76px] space-y-4">
        {/* AI Tutor 3 Modes */}
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-extrabold text-slate-900">Gia sư AI H2Edu (E02)</h2>
              <p className="text-[11px] text-slate-500">RAG từ nội dung PUBLISHED &amp; đã kiểm chứng</p>
            </div>
            <span
              data-testid="tutor-active-mode-badge"
              className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700"
            >
              {mode}
            </span>
          </div>

          <div className="mt-3 grid grid-cols-1 gap-1.5" role="group" aria-label="Chế độ Gia sư AI">
            {TUTOR_MODE_OPTIONS.map((opt) => {
              const selected = mode === opt.mode;
              return (
                <button
                  key={opt.mode}
                  type="button"
                  data-testid={`tutor-mode-${opt.mode}`}
                  aria-pressed={selected}
                  onClick={() => setMode(opt.mode)}
                  className={`rounded-lg border px-2.5 py-2 text-left text-xs transition ${
                    selected
                      ? "border-blue-600 bg-blue-50 font-semibold text-blue-950"
                      : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  <div>{opt.label}</div>
                  <div className="mt-0.5 text-[10px] font-normal text-slate-500">{opt.description}</div>
                </button>
              );
            })}
          </div>

          {examLocked && (
            <div
              role="alert"
              data-testid="tutor-exam-lock-alert"
              className="mt-3 rounded-xl border border-rose-300 bg-rose-50 p-3 text-xs font-semibold text-rose-900"
            >
              403 EXAM_SESSION_ACTIVE — Đang có phòng thi hoạt động ({activeExamAttemptId}), Gia sư AI tạm khóa cho tới khi nộp bài.
            </div>
          )}

          <div className="mt-3 max-h-56 space-y-2 overflow-y-auto rounded-xl border border-slate-100 bg-slate-50 p-2.5">
            {messages.map((msg) => (
              <div
                key={msg.id}
                data-testid={`tutor-message-${msg.sender.toLowerCase()}`}
                className={`rounded-lg px-2.5 py-2 text-xs leading-relaxed ${
                  msg.sender === "AI"
                    ? "border border-slate-200 bg-white text-slate-800"
                    : "ml-6 bg-blue-700 text-white"
                }`}
              >
                <div className="mb-0.5 text-[10px] font-bold opacity-70">
                  {msg.sender === "AI" ? `Gia sư AI (${msg.mode})` : "Bạn"}
                </div>
                <SafeKatexText text={msg.text} />
              </div>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
            <input
              type="text"
              data-testid="tutor-question-input"
              disabled={examLocked}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={examLocked ? "Đã khóa khi đang thi..." : "Hỏi bước giải hoặc công thức..."}
              className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs text-slate-900 focus:border-blue-600 focus:outline-none disabled:bg-slate-100"
            />
            <button
              type="submit"
              data-testid="tutor-send-btn"
              disabled={examLocked || !draft.trim()}
              className="rounded-lg bg-blue-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-800 disabled:opacity-50"
            >
              Hỏi
            </button>
          </form>
        </section>

        {/* Sổ tay câu sai summary drawer */}
        <section
          data-testid="drawer-error-notebook"
          className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs"
        >
          <div className="flex items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-extrabold text-slate-900">Sổ tay câu sai (D05)</h2>
              <p className="text-[11px] text-slate-500">Đúng liên tiếp &ge; 2 câu tương tự &rarr; RESOLVED</p>
            </div>
            <Link
              href="/so-tay-cau-sai"
              className="text-xs font-bold text-blue-700 hover:underline"
            >
              Mở sổ tay &rarr;
            </Link>
          </div>

          <ul className="mt-3 space-y-2">
            {errorNotes.length === 0 ? (
              <li className="rounded-lg bg-emerald-50 p-2.5 text-xs text-emerald-800">
                Chưa có câu sai nào cần khắc phục trong chủ đề này.
              </li>
            ) : (
              errorNotes.map((note) => (
                <li
                  key={note.id}
                  data-testid={`drawer-error-note-${note.id}`}
                  className="rounded-xl border border-slate-200 bg-slate-50/70 p-2.5 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-slate-800">{note.questionCode}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        note.status === "RESOLVED"
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-amber-100 text-amber-900"
                      }`}
                    >
                      {note.status === "RESOLVED"
                        ? "RESOLVED"
                        : `${note.consecutiveCorrectCount}/2 câu đúng`}
                    </span>
                  </div>
                  <div className="mt-1 text-slate-700">
                    <SafeKatexText text={note.summaryLatex} />
                  </div>
                </li>
              ))
            )}
          </ul>
        </section>
      </div>
    </aside>
  );
}
