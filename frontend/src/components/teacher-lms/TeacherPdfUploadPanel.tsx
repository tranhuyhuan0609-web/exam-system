"use client";

import Link from "next/link";
import React, { useState } from "react";
import { workspaceCall } from "@/lib/workspace-api";

export interface TeacherClassOption {
  readonly id: string;
  readonly name: string;
  readonly invite_code?: string | null;
}

export interface TeacherPdfPaperResult {
  readonly assessment_id: string;
  readonly assessment_version_id: string;
  readonly assignment_id: string | null;
  readonly title: string;
  readonly question_count: number;
  readonly skipped_question_count: number;
  readonly duration_minutes: number;
  readonly exam_room_url: string | null;
  readonly assignment_url: string | null;
}

export interface TeacherPdfUploadPanelProps {
  readonly classes: readonly TeacherClassOption[];
  readonly fetchFn?: typeof fetch;
  readonly apiBase?: string;
  readonly onPaperCreated?: (paper: TeacherPdfPaperResult) => void;
}

const VALID_DURATIONS = [15, 30, 45, 60, 90, 120, 150, 180] as const;

const SUBJECT_OPTIONS = [
  { code: "math", label: "Toán học (Trọng tâm)" },
  { code: "physics", label: "Vật lí" },
  { code: "chemistry", label: "Hóa học" },
  { code: "biology", label: "Sinh học" },
] as const;

const GRADE_OPTIONS = [
  { code: "GRADE_12", label: "Lớp 12 / THPTQG" },
  { code: "GRADE_11", label: "Lớp 11" },
  { code: "GRADE_10", label: "Lớp 10" },
] as const;

export function TeacherPdfUploadPanel({
  classes,
  fetchFn,
  apiBase = "",
  onPaperCreated,
}: TeacherPdfUploadPanelProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [subjectCode, setSubjectCode] = useState<string>("math");
  const [gradeBand, setGradeBand] = useState<string>("GRADE_12");
  const [durationMinutes, setDurationMinutes] = useState<number>(45);
  const [selectedClassId, setSelectedClassId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<TeacherPdfPaperResult | null>(null);
  const [previewAttemptId, setPreviewAttemptId] = useState<string | null>(null);
  const [startingPreview, setStartingPreview] = useState(false);

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setErrorMsg(null);
    if (file && !title.trim()) {
      setTitle(file.name.replace(/\.pdf$/i, ""));
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    setResult(null);
    setPreviewAttemptId(null);

    if (!selectedFile) {
      setErrorMsg("Vui lòng chọn file đề PDF (.pdf) trước khi gửi.");
      return;
    }

    const lowerName = selectedFile.name.toLowerCase();
    if (selectedFile.type && selectedFile.type !== "application/pdf" && !lowerName.endsWith(".pdf")) {
      setErrorMsg("Endpoint /teacher/assessments/from-pdf hiện chỉ nhận file .pdf.");
      return;
    }

    const cleanTitle = title.trim() || selectedFile.name.replace(/\.pdf$/i, "") || "Đề kiểm tra PDF";

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("title", cleanTitle);
    formData.append("subject_code", subjectCode);
    formData.append("grade_band", gradeBand);
    formData.append("duration_minutes", String(durationMinutes));
    if (selectedClassId) {
      formData.append("class_id", selectedClassId);
    }

    setSubmitting(true);
    const res = await workspaceCall<TeacherPdfPaperResult>({
      url: "/api/v1/teacher/assessments/from-pdf",
      method: "POST",
      formData,
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setSubmitting(false);

    if (!res.ok) {
      setErrorMsg(res.error);
      return;
    }

    if (res.data) {
      setResult(res.data);
      onPaperCreated?.(res.data);
    }
  };

  const handleStartPreview = async () => {
    if (!result) return;
    setStartingPreview(true);
    setErrorMsg(null);
    const res = await workspaceCall<{ attempt_id?: string }>({
      url: "/api/v1/exam-sessions/start",
      method: "POST",
      json: result.assignment_id
        ? { assessment_id: result.assessment_id, assignment_id: result.assignment_id }
        : { assessment_id: result.assessment_id },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setStartingPreview(false);
    if (!res.ok) {
      setErrorMsg(res.error);
      return;
    }
    if (res.data?.attempt_id) {
      setPreviewAttemptId(res.data.attempt_id);
    }
  };

  return (
    <section
      data-testid="teacher-pdf-upload-panel"
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
    >
      <div className="mb-4">
        <span className="text-[11px] font-bold uppercase tracking-wider text-blue-700">
          C04 Teacher PDF-to-Exam Room · AI Tự Giải &amp; Xuất Bản Tức Thì
        </span>
        <h2 className="mt-1 text-lg font-extrabold text-slate-900">
          Tải đề PDF lên để AI giải đáp án, tạo đề chuẩn &amp; mở phòng thi
        </h2>
        <p className="mt-1 text-xs text-slate-600">
          Hệ thống tách câu hỏi từ file PDF, ưu tiên đáp án sẵn có hoặc nhờ AI giải + kiểm chứng SymPy, rồi xuất bản
          thành đề thi riêng tư. Giáo viên chỉ nhận lại đề thi &amp; link phòng thi (không mở kho ngân hàng chung).
        </p>
      </div>

      {errorMsg && (
        <div
          role="alert"
          data-testid="teacher-pdf-error"
          className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs font-semibold text-rose-900"
        >
          {errorMsg}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
              Chọn file đề PDF (.pdf)
            </label>
            <input
              type="file"
              accept=".pdf,application/pdf"
              data-testid="teacher-pdf-file-input"
              onChange={handleFileChange}
              className="block w-full rounded-xl border border-slate-300 bg-slate-50 px-3 py-1.5 text-xs file:mr-3 file:rounded-lg file:border-0 file:bg-blue-700 file:px-3 file:py-1.5 file:text-xs file:font-bold file:text-white"
            />
          </div>

          <div>
            <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
              Tên bài kiểm tra
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="VD: Kiểm tra 45p Xác suất Toán 12"
              data-testid="teacher-pdf-title-input"
              className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-900"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
              Môn học
            </label>
            <select
              value={subjectCode}
              onChange={(e) => setSubjectCode(e.target.value)}
              data-testid="teacher-pdf-subject-select"
              className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-900"
            >
              {SUBJECT_OPTIONS.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
              Khối lớp
            </label>
            <select
              value={gradeBand}
              onChange={(e) => setGradeBand(e.target.value)}
              data-testid="teacher-pdf-grade-select"
              className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-900"
            >
              {GRADE_OPTIONS.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
              Thời gian làm bài
            </label>
            <select
              value={durationMinutes}
              onChange={(e) => setDurationMinutes(Number(e.target.value))}
              data-testid="teacher-pdf-duration-select"
              className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-900"
            >
              {VALID_DURATIONS.map((mins) => (
                <option key={mins} value={mins}>
                  {mins} phút
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
              Giao ngay cho lớp (tùy chọn)
            </label>
            <select
              value={selectedClassId}
              onChange={(e) => setSelectedClassId(e.target.value)}
              data-testid="teacher-pdf-class-select"
              className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-900"
            >
              <option value="">-- Chỉ tạo &amp; xuất bản đề --</option>
              {classes.map((cls) => (
                <option key={cls.id} value={cls.id}>
                  {cls.name} {cls.invite_code ? `(${cls.invite_code})` : ""}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
          <span className="text-[11px] text-slate-500">
            * Câu nào chưa xác định được đáp án chắc chắn sẽ được thống kê vào mục <code>skipped_question_count</code>.
          </span>
          <button
            type="submit"
            disabled={submitting}
            data-testid="teacher-pdf-submit-btn"
            className="rounded-xl bg-blue-700 px-5 py-2.5 text-xs font-bold text-white shadow-xs transition hover:bg-blue-800 disabled:opacity-50"
          >
            {submitting ? "Đang tách đề & AI giải..." : "Tách đề, AI tự giải & Mở phòng thi"}
          </button>
        </div>
      </form>

      {result && (
        <div
          data-testid="teacher-pdf-result-panel"
          className="mt-5 rounded-2xl border border-emerald-300 bg-emerald-50 p-4 text-xs text-emerald-950"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-extrabold text-emerald-900">
                Đã tạo &amp; xuất bản đề: {result.title}
              </h3>
              <p className="mt-1">
                Số câu đưa vào đề: <strong>{result.question_count} câu</strong> · Bỏ qua:{" "}
                <strong>{result.skipped_question_count} câu</strong> · Thời lượng:{" "}
                <strong>{result.duration_minutes} phút</strong>
              </p>
              <p className="mt-0.5 font-mono text-[11px] text-emerald-800">
                assessment_id: {result.assessment_id}
                {result.assignment_id ? ` · assignment_id: ${result.assignment_id}` : ""}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {result.assignment_url && (
                <Link
                  href={result.assignment_url}
                  data-testid="teacher-pdf-open-class-link"
                  className="rounded-xl bg-emerald-700 px-3.5 py-2 font-bold text-white hover:bg-emerald-800"
                >
                  Mở lớp &amp; Giao bài →
                </Link>
              )}
              <button
                type="button"
                onClick={handleStartPreview}
                disabled={startingPreview}
                data-testid="teacher-pdf-preview-room-btn"
                className="rounded-xl border border-emerald-400 bg-white px-3.5 py-2 font-bold text-emerald-900 hover:bg-emerald-100 disabled:opacity-50"
              >
                {startingPreview ? "Đang tạo lượt thi..." : "Xem thử Phòng thi"}
              </button>
            </div>
          </div>

          {previewAttemptId && (
            <div className="mt-3 flex items-center justify-between rounded-xl border border-emerald-300 bg-white px-3 py-2">
              <span>Phòng thi thử đã sẵn sàng!</span>
              <Link
                href={`/phong-thi/${previewAttemptId}`}
                data-testid="teacher-pdf-enter-room-link"
                className="font-bold text-blue-700 underline"
              >
                Vào phòng thi /phong-thi/{previewAttemptId} →
              </Link>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
