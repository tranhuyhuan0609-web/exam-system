"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useState } from "react";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  AUTH_CHANGED_EVENT,
  getAuthSession,
  hasAuthToken,
  isDemoModeClient,
  type AuthSession,
} from "@/lib/auth-client";
import type { UserTier } from "@/lib/ui-state-machine";
import { shouldHydrateLive, workspaceCall } from "@/lib/workspace-api";

export interface ExamImportListItem {
  readonly id: string;
  readonly extraction_run_id: string;
  readonly document_version_id: string | null;
  readonly title: string | null;
  readonly mime_type: string | null;
  readonly page_count: number | null;
  readonly status: string;
  readonly rights_status: string;
  readonly section_count: number;
  readonly question_count: number;
  readonly created_at: string;
}

export interface ImportJobSummary {
  readonly id: string;
  readonly source_id: string | null;
  readonly document_version_id: string | null;
  readonly job_type: string;
  readonly job_status: string;
  readonly current_stage: string | null;
  readonly attempt_count: number;
  readonly last_error_code: string | null;
  readonly title: string | null;
  readonly mime_type: string | null;
  readonly page_count: number | null;
  readonly rights_status: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

interface ExamImportListResponse {
  readonly items: readonly ExamImportListItem[];
  readonly next_cursor: string | null;
  readonly has_more: boolean;
}

interface ImportJobListResponse {
  readonly items: readonly ImportJobSummary[];
  readonly next_cursor: string | null;
  readonly has_more: boolean;
}

interface UploadDocumentResponse {
  readonly source_document_id: string;
  readonly document_version_id: string;
  readonly acquisition_job_id?: string | null;
}

interface ExtractedQuestionPayload {
  readonly question_no: number;
  readonly points: number;
  readonly page_number: number;
  readonly item_type?: string;
  readonly stem_md?: string | null;
  readonly options_md?: readonly string[];
  readonly paired_answer?: string | null;
  readonly bbox_json?: Record<string, unknown> | null;
  readonly original_question_label?: string | null;
}

interface ExtractedSectionPayload {
  readonly section_code: string;
  readonly title: string;
  readonly questions: readonly ExtractedQuestionPayload[];
}

interface ExtractDocumentResponse {
  readonly id: string;
  readonly extracted_structure?: {
    readonly pages?: ReadonlyArray<{
      readonly page_no: number;
      readonly width_pt: number;
      readonly height_pt: number;
      readonly heading?: string;
    }>;
    readonly sections?: readonly ExtractedSectionPayload[];
  } | null;
}

interface CreateExamImportResponse {
  readonly id: string;
  readonly status: string;
}

interface EnqueueImportResponse {
  readonly is_duplicate: boolean;
  readonly idempotent_replay: boolean;
  readonly job: {
    readonly id: string;
    readonly job_status: string;
    readonly job_type: string;
  };
  readonly document_version: {
    readonly id: string;
    readonly title: string | null;
    readonly mime_type: string;
  };
}

type UploadPipelineMode = "BBOX_PIPELINE" | "A01_QUEUE";

const EXAM_STATUS_BADGE: Readonly<Record<string, string>> = {
  PENDING_REVIEW: "bg-amber-100 text-amber-900 border-amber-200",
  APPROVED: "bg-emerald-100 text-emerald-900 border-emerald-200",
  REJECTED: "bg-rose-100 text-rose-900 border-rose-200",
};

const JOB_STATUS_BADGE: Readonly<Record<string, string>> = {
  SUCCEEDED: "bg-emerald-100 text-emerald-900 border-emerald-200",
  FAILED: "bg-rose-100 text-rose-900 border-rose-200",
  CANCELLED: "bg-slate-200 text-slate-700 border-slate-300",
  QUEUED: "bg-blue-100 text-blue-900 border-blue-200",
  PROCESSING: "bg-indigo-100 text-indigo-900 border-indigo-200",
};

const DEMO_EXAM_IMPORTS: readonly ExamImportListItem[] = [
  {
    id: "demo-run",
    extraction_run_id: "demo-extract-1",
    document_version_id: "demo-doc-ver-1",
    title: "Đề Minh họa Toán THPTQG 2025 (Demo BBox)",
    mime_type: "application/pdf",
    page_count: 4,
    status: "PENDING_REVIEW",
    rights_status: "OWNED",
    section_count: 1,
    question_count: 3,
    created_at: "2026-11-15T00:00:00Z",
  },
];

const DEMO_IMPORT_JOBS: readonly ImportJobSummary[] = [
  {
    id: "demo-job-1",
    source_id: "demo-src-1",
    document_version_id: "demo-doc-ver-1",
    job_type: "EXAM_IMPORT",
    job_status: "SUCCEEDED",
    current_stage: "PARSED",
    attempt_count: 1,
    last_error_code: null,
    title: "Đề Minh họa Toán THPTQG 2025 (Demo)",
    mime_type: "application/pdf",
    page_count: 4,
    rights_status: "OWNED",
    created_at: "2026-11-15T00:00:00Z",
    updated_at: "2026-11-15T00:01:00Z",
  },
];

export interface ImportsHubShellProps {
  initialExamImports?: readonly ExamImportListItem[];
  initialImportJobs?: readonly ImportJobSummary[];
  initialUserTier?: UserTier;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function ImportsHubShell({
  initialExamImports,
  initialImportJobs,
  initialUserTier = "TEACHER_PRO",
  fetchFn,
  apiBase = "",
}: ImportsHubShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);

  const [pipelineMode, setPipelineMode] = useState<UploadPipelineMode>("BBOX_PIPELINE");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadStep, setUploadStep] = useState<string | null>(null);
  const [createdImportId, setCreatedImportId] = useState<string | null>(null);

  const [examImports, setExamImports] = useState<readonly ExamImportListItem[]>(
    () => initialExamImports ?? [],
  );
  const [examStatusFilter, setExamStatusFilter] = useState("");
  const [examNextCursor, setExamNextCursor] = useState<string | null>(null);
  const [examHasMore, setExamHasMore] = useState(false);
  const [loadingExams, setLoadingExams] = useState(false);

  const [importJobs, setImportJobs] = useState<readonly ImportJobSummary[]>(
    () => initialImportJobs ?? [],
  );
  const [jobStatusFilter, setJobStatusFilter] = useState("");
  const [jobTypeFilter, setJobTypeFilter] = useState("");
  const [jobNextCursor, setJobNextCursor] = useState<string | null>(null);
  const [jobHasMore, setJobHasMore] = useState(false);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [busyJobId, setBusyJobId] = useState<string | null>(null);

  const [feedback, setFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);

  useEffect(() => {
    const syncAuth = () => {
      setSession(getAuthSession());
      setHasToken(hasAuthToken());
      setDemoMode(isDemoModeClient());
      setAuthChecked(true);
    };
    syncAuth();
    window.addEventListener(AUTH_CHANGED_EVENT, syncAuth);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, syncAuth);
  }, []);

  const loadExamImports = useCallback(
    async (cursor?: string | null, append = false) => {
      if (initialExamImports) return;
      if (!shouldHydrateLive({ hasInitialData: false, apiBase })) {
        if (isDemoModeClient()) {
          setExamImports(DEMO_EXAM_IMPORTS);
          setExamNextCursor(null);
          setExamHasMore(false);
        } else {
          setExamImports([]);
          setExamNextCursor(null);
          setExamHasMore(false);
        }
        return;
      }

      setLoadingExams(true);
      const params = new URLSearchParams({ limit: "20" });
      if (examStatusFilter) params.set("status", examStatusFilter);
      if (cursor) params.set("cursor", cursor);

      const res = await workspaceCall<ExamImportListResponse>({
        url: `/api/v1/exam-imports?${params.toString()}`,
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      });
      setLoadingExams(false);

      if (!res.ok) {
        setApiError(res.error);
        setApiErrorStatus(res.status);
        return;
      }

      const rows = Array.isArray(res.data?.items) ? res.data.items : [];
      setExamImports((prev) => (append ? [...prev, ...rows] : rows));
      setExamNextCursor(res.data?.next_cursor ?? null);
      setExamHasMore(Boolean(res.data?.has_more));
    },
    [apiBase, examStatusFilter, fetchFn, initialExamImports],
  );

  const loadImportJobs = useCallback(
    async (cursor?: string | null, append = false) => {
      if (initialImportJobs) return;
      if (!shouldHydrateLive({ hasInitialData: false, apiBase })) {
        if (isDemoModeClient()) {
          setImportJobs(DEMO_IMPORT_JOBS);
          setJobNextCursor(null);
          setJobHasMore(false);
        } else {
          setImportJobs([]);
          setJobNextCursor(null);
          setJobHasMore(false);
        }
        return;
      }

      setLoadingJobs(true);
      const params = new URLSearchParams({ limit: "20" });
      if (jobStatusFilter) params.set("job_status", jobStatusFilter);
      if (jobTypeFilter) params.set("job_type", jobTypeFilter);
      if (cursor) params.set("cursor", cursor);

      const res = await workspaceCall<ImportJobListResponse>({
        url: `/api/v1/imports?${params.toString()}`,
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      });
      setLoadingJobs(false);

      if (!res.ok) {
        setApiError(res.error);
        setApiErrorStatus(res.status);
        return;
      }

      const rows = Array.isArray(res.data?.items) ? res.data.items : [];
      setImportJobs((prev) => (append ? [...prev, ...rows] : rows));
      setJobNextCursor(res.data?.next_cursor ?? null);
      setJobHasMore(Boolean(res.data?.has_more));
    },
    [apiBase, fetchFn, initialImportJobs, jobStatusFilter, jobTypeFilter],
  );

  useEffect(() => {
    if (!authChecked) return;
    void loadExamImports(null, false);
  }, [authChecked, loadExamImports, hasToken]);

  useEffect(() => {
    if (!authChecked) return;
    void loadImportJobs(null, false);
  }, [authChecked, loadImportJobs, hasToken]);

  const handleUploadSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setApiError(null);
    setApiErrorStatus(null);
    setFeedback(null);
    setCreatedImportId(null);

    if (!selectedFile) {
      setApiError("Vui lòng chọn file đề (.pdf, .docx, .doc, .jpg, .png, .webp) trước khi tải lên.");
      setApiErrorStatus(422);
      return;
    }

    const lowerName = selectedFile.name.toLowerCase();
    const allowedExt = [".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".webp"].some((ext) =>
      lowerName.endsWith(ext),
    );
    const allowedMime = new Set([
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/msword",
      "image/jpeg",
      "image/png",
      "image/webp",
    ]);
    if (!allowedExt && !(selectedFile.type && allowedMime.has(selectedFile.type))) {
      setApiError(
        "Định dạng chưa hỗ trợ ở luồng Studio (.zip hoặc file khác sẽ bị từ chối 422). Chỉ nhận .pdf, .docx, .doc, .jpg, .png, .webp.",
      );
      setApiErrorStatus(422);
      return;
    }

    if (!hasToken) {
      setApiError("Vui lòng đăng nhập tài khoản biên soạn (CONTENT_AUTHOR / ADMIN) trước khi tải file đề lên API.");
      setApiErrorStatus(401);
      return;
    }

    setUploading(true);
    const effectiveTitle =
      uploadTitle.trim() || selectedFile.name.replace(/\.(pdf|docx|doc|jpe?g|png|webp)$/i, "");

    if (pipelineMode === "A01_QUEUE") {
      setUploadStep("Đang đẩy file PDF vào Hàng đợi A01 (POST /api/v1/imports)...");
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("job_type", "EXAM_IMPORT");
      if (effectiveTitle) formData.append("title", effectiveTitle);
      if (idempotencyKey.trim()) formData.append("idempotency_key", idempotencyKey.trim());

      const res = await workspaceCall<EnqueueImportResponse>({
        url: "/api/v1/imports",
        method: "POST",
        formData,
        fetchFn,
        apiBase,
        redirectOn401: false,
      });

      setUploading(false);
      setUploadStep(null);

      if (!res.ok) {
        setApiError(res.error);
        setApiErrorStatus(res.status);
        return;
      }

      if (!res.data) {
        setApiError("Phản hồi API tạo import rỗng.");
        return;
      }

      setFeedback(
        res.data.idempotent_replay
          ? `Đã phát hiện Idempotent Replay — job ${res.data.job.id} (${res.data.job.job_status}).`
          : res.data.is_duplicate
            ? `File trùng nội dung SHA-256 — đã liên kết job ${res.data.job.id} (${res.data.job.job_status}).`
            : `Đã đưa vào hàng đợi thành công: job ${res.data.job.id} (${res.data.job.job_status}).`,
      );
      await loadImportJobs(null, false);
      return;
    }

    // Pipeline: POST /api/v1/documents -> POST /api/v1/documents/{source_document_id}/extract -> POST /api/v1/exam-imports
    setUploadStep("Bước 1/3: Đang tải tài liệu PDF lên kho nguồn (POST /api/v1/documents)...");
    const docForm = new FormData();
    docForm.append("file", selectedFile);
    if (effectiveTitle) docForm.append("title", effectiveTitle);

    const uploadRes = await workspaceCall<UploadDocumentResponse>({
      url: "/api/v1/documents",
      method: "POST",
      formData: docForm,
      fetchFn,
      apiBase,
      redirectOn401: false,
    });

    if (!uploadRes.ok) {
      setUploading(false);
      setUploadStep(null);
      setApiError(uploadRes.error);
      setApiErrorStatus(uploadRes.status);
      return;
    }

    if (!uploadRes.data?.source_document_id) {
      setUploading(false);
      setUploadStep(null);
      setApiError("Thiếu source_document_id từ API /api/v1/documents.");
      return;
    }

    setUploadStep("Bước 2/3: Đang chạy trích xuất cấu trúc đề (POST /extract)...");
    const extractRes = await workspaceCall<ExtractDocumentResponse>({
      url: `/api/v1/documents/${uploadRes.data.source_document_id}/extract`,
      method: "POST",
      json: {},
      fetchFn,
      apiBase,
      redirectOn401: false,
    });

    if (!extractRes.ok) {
      setUploading(false);
      setUploadStep(null);
      setApiError(extractRes.error);
      setApiErrorStatus(extractRes.status);
      return;
    }

    if (!extractRes.data?.id) {
      setUploading(false);
      setUploadStep(null);
      setApiError("Thiếu extraction_run_id từ API /extract.");
      return;
    }

    setUploadStep("Bước 3/3: Đang khởi tạo phiên duyệt BBox (POST /api/v1/exam-imports)...");
    const extractedSections = extractRes.data.extracted_structure?.sections ?? [];
    const hasExtractedQuestions = extractedSections.some((sec) => sec.questions.length > 0);
    const sectionsPayload: ExtractedSectionPayload[] = hasExtractedQuestions
      ? extractedSections.map((sec) => ({
          section_code: sec.section_code,
          title: sec.title,
          questions: sec.questions.map((q) => ({
            question_no: q.question_no,
            points: q.points,
            page_number: q.page_number,
            item_type: q.item_type ?? "SINGLE_CHOICE",
            stem_md: q.stem_md ?? `Câu ${q.question_no}`,
            options_md: [...(q.options_md ?? [])],
            paired_answer: q.paired_answer ?? null,
            bbox_json: q.bbox_json ?? undefined,
            original_question_label: q.original_question_label ?? `Câu ${q.question_no}`,
          })),
        }))
      : [
          {
            section_code: "PART_I_MCQ",
            title: effectiveTitle,
            questions: [
              {
                question_no: 1,
                points: 10.0,
                page_number: 1,
                item_type: "SINGLE_CHOICE",
                stem_md: "Câu 1 (mới nạp — rà soát & chỉnh BBox)",
                options_md: ["A", "B", "C", "D"],
                paired_answer: null,
                bbox_json: { x0: 40, y0: 120, x1: 550, y1: 240 },
                original_question_label: "Câu 1",
              },
            ],
          },
        ];
    const totalPoints = sectionsPayload
      .flatMap((s) => s.questions)
      .reduce((acc, q) => acc + (Number.isFinite(q.points) ? q.points : 0), 0);

    const sessionRes = await workspaceCall<CreateExamImportResponse>({
      url: "/api/v1/exam-imports",
      method: "POST",
      json: {
        extraction_run_id: extractRes.data.id,
        expected_total_points: totalPoints > 0 ? Number(totalPoints.toFixed(2)) : 10.0,
        create_question_candidates: false,
        sections: sectionsPayload,
      },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });

    setUploading(false);
    setUploadStep(null);

    if (!sessionRes.ok) {
      setApiError(sessionRes.error);
      setApiErrorStatus(sessionRes.status);
      return;
    }

    if (!sessionRes.data?.id) {
      setApiError("Thiếu exam_import id từ API /api/v1/exam-imports.");
      return;
    }

    setCreatedImportId(sessionRes.data.id);
    setFeedback(`Đã tạo xong phiên duyệt BBox (${sessionRes.data.status}). Bạn có thể mở duyệt ngay!`);
    await Promise.all([loadExamImports(null, false), loadImportJobs(null, false)]);
  };

  const handleJobAction = async (jobId: string, action: "retry" | "cancel") => {
    setBusyJobId(jobId);
    setApiError(null);
    setApiErrorStatus(null);
    setFeedback(null);

    const res = await workspaceCall<unknown>({
      url: `/api/v1/imports/${jobId}/${action}`,
      method: "POST",
      json: action === "cancel" ? { reason: "Cancelled by staff" } : {},
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setBusyJobId(null);

    if (!res.ok) {
      setApiError(res.error);
      setApiErrorStatus(res.status);
      return;
    }

    setFeedback(action === "retry" ? `Đã gửi yêu cầu thử lại job ${jobId}.` : `Đã hủy job ${jobId}.`);
    await loadImportJobs(null, false);
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <StudioHeader
        activeRoute="ws4-imports"
        userTier={userTier}
        onTierChange={setUserTier}
      />

      <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <div>
            <h1 className="text-xl font-extrabold tracking-tight text-slate-900">
              Trung tâm Nạp đề PDF & Phiên duyệt BBox
            </h1>
            <p className="mt-1 text-xs text-slate-600">
              Tải đề thi PDF, theo dõi hàng đợi xử lý A01 và mở màn hình duyệt BBox / SymPy side-by-side.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/studio/questions"
              className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
            >
              Ngân hàng câu hỏi
            </Link>
            <Link
              href="/studio/questions/new"
              className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              + Soạn câu hỏi C01
            </Link>
          </div>
        </div>

        <WorkspaceApiAlert
          message={apiError}
          status={apiErrorStatus}
          onDismiss={() => {
            setApiError(null);
            setApiErrorStatus(null);
          }}
        />

        {feedback && (
          <div
            data-testid="imports-feedback"
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-900"
          >
            <span>{feedback}</span>
            {createdImportId && (
              <Link
                href={`/studio/imports/${createdImportId}/review`}
                data-testid="open-created-bbox-link"
                className="rounded-lg bg-emerald-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-800"
              >
                Mở duyệt BBox ngay →
              </Link>
            )}
          </div>
        )}

        {!hasToken && !demoMode && !initialExamImports && !initialImportJobs && (
          <section
            data-testid="imports-auth-prompt"
            className="rounded-2xl border border-amber-200 bg-amber-50/80 p-5 text-sm text-amber-950"
          >
            <p className="font-bold">Cần đăng nhập tài khoản biên soạn để nạp đề và quản lý hàng đợi</p>
            <p className="mt-1 text-xs text-amber-900">
              API <code>/api/v1/imports</code> và <code>/api/v1/exam-imports</code> yêu cầu quyền{" "}
              <code>CONTENT_AUTHOR / REVIEWER / ADMIN</code> (giáo viên tạo đề từ PDF qua mục Giáo viên LMS, học sinh nhận HTTP 403).
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                href="/dang-nhap"
                className="rounded-xl bg-amber-700 px-3.5 py-2 text-xs font-bold text-white hover:bg-amber-800"
              >
                Đi tới Đăng nhập
              </Link>
              <Link
                href="/studio/imports?demo=1"
                className="rounded-xl border border-amber-300 bg-white px-3.5 py-2 text-xs font-bold text-amber-900 hover:bg-amber-100/60"
              >
                Xem thử giao diện (?demo=1)
              </Link>
            </div>
          </section>
        )}

        <section
          aria-label="Form nạp file đề PDF, Word hoặc Ảnh"
          className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
        >
          <div className="mb-4 rounded-xl border border-sky-200 bg-sky-50 px-3.5 py-2.5 text-xs text-sky-950">
            <span className="font-bold">Định dạng file hỗ trợ: </span>
            Luồng V11.3 (<code>/api/v1/documents</code> → <code>/extract</code> → <code>/exam-imports</code>) hỗ trợ{" "}
            <code>.pdf</code>, <code>.docx</code>, <code>.doc</code> (qua LibreOffice) và ảnh chụp{" "}
            <code>.jpg</code> / <code>.png</code> / <code>.webp</code> (OCR + làm sạch nền). File <code>.zip</code> sẽ bị từ chối.
          </div>

          <form onSubmit={handleUploadSubmit} className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <label
                className={`flex cursor-pointer items-start gap-2.5 rounded-xl border p-3 text-xs ${
                  pipelineMode === "BBOX_PIPELINE"
                    ? "border-indigo-600 bg-indigo-50/70"
                    : "border-slate-200 bg-white"
                }`}
              >
                <input
                  type="radio"
                  name="uploadPipeline"
                  checked={pipelineMode === "BBOX_PIPELINE"}
                  onChange={() => setPipelineMode("BBOX_PIPELINE")}
                  className="mt-0.5"
                />
                <span>
                  <strong className="block text-slate-900">
                    Nạp PDF & Mở phiên Duyệt BBox (Khuyên dùng)
                  </strong>
                  <span className="text-slate-600">
                    Chạy <code>/documents</code> → <code>/extract</code> → <code>/exam-imports</code> để duyệt BBox và bóc tách câu hỏi.
                  </span>
                </span>
              </label>

              <label
                className={`flex cursor-pointer items-start gap-2.5 rounded-xl border p-3 text-xs ${
                  pipelineMode === "A01_QUEUE"
                    ? "border-indigo-600 bg-indigo-50/70"
                    : "border-slate-200 bg-white"
                }`}
              >
                <input
                  type="radio"
                  name="uploadPipeline"
                  checked={pipelineMode === "A01_QUEUE"}
                  onChange={() => setPipelineMode("A01_QUEUE")}
                  className="mt-0.5"
                />
                <span>
                  <strong className="block text-slate-900">
                    Chỉ đẩy vào Hàng đợi Ingestion A01
                  </strong>
                  <span className="text-slate-600">
                    Gọi trực tiếp <code>POST /api/v1/imports</code> (hỗ trợ idempotency key & dedup SHA-256).
                  </span>
                </span>
              </label>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                  Chọn file đề (.pdf, .docx, .doc, ảnh)
                </label>
                <input
                  type="file"
                  accept=".pdf,.docx,.doc,.jpg,.jpeg,.png,.webp,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,image/jpeg,image/png,image/webp"
                  data-testid="imports-file-input"
                  onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
                  className="block w-full rounded-xl border border-slate-300 bg-slate-50 px-3 py-1.5 text-xs file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-700 file:px-2.5 file:py-1 file:text-xs file:font-bold file:text-white"
                />
              </div>

              <div>
                <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                  Tiêu đề đề thi (tùy chọn)
                </label>
                <input
                  type="text"
                  value={uploadTitle}
                  onChange={(e) => setUploadTitle(e.target.value)}
                  placeholder="VD: Đề thi thử Sở HN 2025"
                  data-testid="imports-title-input"
                  className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs"
                />
              </div>

              <div>
                <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                  Idempotency Key (cho A01)
                </label>
                <input
                  type="text"
                  value={idempotencyKey}
                  onChange={(e) => setIdempotencyKey(e.target.value)}
                  disabled={pipelineMode !== "A01_QUEUE"}
                  placeholder="Tự bỏ trống nếu không cần"
                  data-testid="imports-idempotency-input"
                  className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs disabled:bg-slate-100"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={uploading}
                data-testid="imports-upload-submit-btn"
                className="rounded-xl bg-indigo-700 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-indigo-800 disabled:opacity-50"
              >
                {uploading
                  ? "Đang xử lý..."
                  : pipelineMode === "BBOX_PIPELINE"
                    ? "Tải PDF & Tạo phiên Duyệt BBox"
                    : "Đẩy vào Hàng đợi /imports"}
              </button>
              {uploadStep && <span className="text-xs font-medium text-indigo-700">{uploadStep}</span>}
              {session && (
                <span className="ml-auto text-xs text-slate-500">
                  Vai trò: {session.roles.join(", ")}
                </span>
              )}
            </div>
          </form>
        </section>

        <div className="grid gap-5 lg:grid-cols-2">
          <section
            aria-label="Danh sách phiên duyệt BBox"
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
          >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-extrabold text-slate-900">
                  Phiên duyệt BBox (<code>GET /api/v1/exam-imports</code>)
                </h2>
                <p className="text-[11px] text-slate-500">
                  Nhấn &ldquo;Mở duyệt BBox&rdquo; để rà soát tọa độ câu hỏi & công thức toán.
                </p>
              </div>
              <select
                value={examStatusFilter}
                onChange={(e) => setExamStatusFilter(e.target.value)}
                data-testid="exam-imports-status-select"
                className="rounded-xl border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
              >
                <option value="">Tất cả trạng thái</option>
                <option value="PENDING_REVIEW">PENDING_REVIEW (Chờ duyệt)</option>
                <option value="APPROVED">APPROVED (Đã duyệt)</option>
                <option value="REJECTED">REJECTED (Từ chối)</option>
              </select>
            </div>

            {loadingExams ? (
              <p className="text-xs text-slate-500">Đang tải danh sách phiên duyệt...</p>
            ) : examImports.length === 0 ? (
              <p data-testid="exam-imports-empty" className="text-xs text-slate-600">
                Chưa có phiên duyệt BBox nào. Tải file PDF ở form phía trên hoặc chạy <code>seed_demo</code>.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {examImports.map((item) => {
                  const badgeClass =
                    EXAM_STATUS_BADGE[item.status] ??
                    "bg-slate-100 text-slate-700 border-slate-200";
                  return (
                    <li
                      key={item.id}
                      data-testid={`exam-import-row-${item.id}`}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="truncate text-xs font-extrabold text-slate-900">
                            {item.title ?? `Phiên ${item.id.slice(0, 8)}`}
                          </span>
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${badgeClass}`}>
                            {item.status}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] text-slate-600">
                          {item.section_count} phần · {item.question_count} câu hỏi ·{" "}
                          {item.page_count ?? 1} trang · Bản quyền: {item.rights_status}
                        </p>
                      </div>
                      <Link
                        href={`/studio/imports/${item.id}/review`}
                        data-testid={`review-link-${item.id}`}
                        className="rounded-xl bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-800"
                      >
                        Mở duyệt BBox
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}

            {examHasMore && examNextCursor && (
              <button
                type="button"
                onClick={() => void loadExamImports(examNextCursor, true)}
                className="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
              >
                Tải thêm phiên duyệt BBox
              </button>
            )}
          </section>

          <section
            aria-label="Hàng đợi nhập tài liệu A01"
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
          >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-extrabold text-slate-900">
                  Hàng đợi Nhập tài liệu (<code>GET /api/v1/imports</code>)
                </h2>
                <p className="text-[11px] text-slate-500">
                  Theo dõi trạng thái bóc tách, thử lại khi lỗi hoặc hủy job đang chờ.
                </p>
              </div>
              <div className="flex gap-2">
                <select
                  value={jobStatusFilter}
                  onChange={(e) => setJobStatusFilter(e.target.value)}
                  data-testid="import-jobs-status-select"
                  className="rounded-xl border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                >
                  <option value="">Mọi trạng thái</option>
                  <option value="QUEUED">QUEUED</option>
                  <option value="PROCESSING">PROCESSING</option>
                  <option value="SUCCEEDED">SUCCEEDED</option>
                  <option value="FAILED">FAILED</option>
                  <option value="CANCELLED">CANCELLED</option>
                </select>
                <select
                  value={jobTypeFilter}
                  onChange={(e) => setJobTypeFilter(e.target.value)}
                  data-testid="import-jobs-type-select"
                  className="rounded-xl border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                >
                  <option value="">Mọi loại job</option>
                  <option value="EXAM_IMPORT">EXAM_IMPORT</option>
                  <option value="DOCUMENT_UPLOAD">DOCUMENT_UPLOAD</option>
                </select>
              </div>
            </div>

            {loadingJobs ? (
              <p className="text-xs text-slate-500">Đang tải danh sách job...</p>
            ) : importJobs.length === 0 ? (
              <p data-testid="import-jobs-empty" className="text-xs text-slate-600">
                Chưa có job nhập tài liệu nào.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {importJobs.map((job) => {
                  const badgeClass =
                    JOB_STATUS_BADGE[job.job_status] ??
                    "bg-slate-100 text-slate-700 border-slate-200";
                  const canRetry = job.job_status === "FAILED" || job.job_status === "CANCELLED";
                  const canCancel = job.job_status === "QUEUED" || job.job_status === "PROCESSING";
                  return (
                    <li
                      key={job.id}
                      data-testid={`import-job-row-${job.id}`}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="truncate text-xs font-extrabold text-slate-900">
                            {job.title ?? `Job ${job.id.slice(0, 8)}`}
                          </span>
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${badgeClass}`}>
                            {job.job_status}
                          </span>
                          <span className="rounded bg-slate-200/70 px-1.5 py-0.5 text-[10px] font-semibold text-slate-700">
                            {job.job_type}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] text-slate-600">
                          Stage: {job.current_stage ?? "—"} · Lần thử: {job.attempt_count}
                          {job.last_error_code ? ` · Lỗi: ${job.last_error_code}` : ""}
                        </p>
                      </div>

                      <div className="flex items-center gap-1.5">
                        {canRetry && (
                          <button
                            type="button"
                            disabled={busyJobId === job.id || !hasToken}
                            onClick={() => void handleJobAction(job.id, "retry")}
                            data-testid={`retry-job-${job.id}`}
                            className="rounded-lg border border-indigo-200 bg-white px-2.5 py-1 text-[11px] font-bold text-indigo-800 hover:bg-indigo-50 disabled:opacity-50"
                          >
                            Thử lại
                          </button>
                        )}
                        {canCancel && (
                          <button
                            type="button"
                            disabled={busyJobId === job.id || !hasToken}
                            onClick={() => void handleJobAction(job.id, "cancel")}
                            data-testid={`cancel-job-${job.id}`}
                            className="rounded-lg border border-rose-200 bg-white px-2.5 py-1 text-[11px] font-bold text-rose-800 hover:bg-rose-50 disabled:opacity-50"
                          >
                            Hủy
                          </button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            {jobHasMore && jobNextCursor && (
              <button
                type="button"
                onClick={() => void loadImportJobs(jobNextCursor, true)}
                className="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
              >
                Tải thêm job hàng đợi
              </button>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
