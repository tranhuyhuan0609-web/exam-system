"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useState } from "react";
import { AUTH_CHANGED_EVENT, getAuthSession, isDemoModeClient, type AuthSession } from "@/lib/auth-client";
import { isUuid, workspaceCall } from "@/lib/workspace-api";

export interface LiveClassItem {
  readonly id: string;
  readonly name: string;
  readonly invite_code: string | null;
  readonly active_student_count: number;
}

export interface LiveAssignmentItem {
  readonly id: string;
  readonly class_id: string | null;
  readonly assessment_id: string | null;
  readonly assessment_title: string | null;
  readonly due_at: string | null;
  readonly max_attempts: number;
}

export interface LivePaperItem {
  readonly id: string;
  readonly title: string;
  readonly subject_code: string;
}

export interface LiveAttemptItem {
  readonly attempt_id: string;
  readonly assessment_title: string;
  readonly assignment_id: string | null;
  readonly status: string;
  readonly deadline_at: string;
}

const STAFF_ROLES = new Set([
  "TEACHER",
  "EDITOR",
  "REVIEWER",
  "ADMIN",
  "SYS_ADMIN",
  "TENANT_ADMIN",
  "PLATFORM_ADMIN",
  "SUPER_ADMIN",
]);

export function isStaffSession(session: Pick<AuthSession, "roles"> | null): boolean {
  return Boolean(session?.roles.some((role) => STAFF_ROLES.has(role.toUpperCase())));
}

export interface LiveWorkspacePanelProps {
  fetchFn?: typeof fetch;
  apiBase?: string;
  /** Where to go after a sitting starts; defaults to a full navigation to /phong-thi/{attempt_id}. */
  onOpenAttempt?: (attemptId: string) => void;
}

function openAttemptByDefault(attemptId: string): void {
  if (typeof window !== "undefined") window.location.assign(`/phong-thi/${attemptId}`);
}

/**
 * The signed-in member's own data from the backend, every item with its real UUID: classes, assignments, published
 * papers and attempts. Renders nothing for a visitor without a session or on a `?demo=1` page, so the static
 * demo links of the home page stay the only way in there.
 */
interface LiveExamImportItem {
  readonly id: string;
  readonly title?: string | null;
  readonly status?: string;
  readonly question_count?: number;
}

export function LiveWorkspacePanel({ fetchFn, apiBase = "", onOpenAttempt }: LiveWorkspacePanelProps) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [classes, setClasses] = useState<readonly LiveClassItem[]>([]);
  const [assignments, setAssignments] = useState<readonly LiveAssignmentItem[]>([]);
  const [papers, setPapers] = useState<readonly LivePaperItem[]>([]);
  const [attempts, setAttempts] = useState<readonly LiveAttemptItem[]>([]);
  const [examImports, setExamImports] = useState<readonly LiveExamImportItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startingKey, setStartingKey] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => setSession(getAuthSession());
    sync();
    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, sync);
  }, []);

  const staff = isStaffSession(session);
  const active = Boolean(session) && apiBase !== "__OFFLINE__" && !isDemoModeClient();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const listOf = async <T extends object>(url: string, key: keyof T) => {
      const res = await workspaceCall<T[]>({ url, method: "GET", fetchFn, apiBase, redirectOn401: false });
      if (!res.ok) return { rows: [] as T[], failure: res.error };
      const rows = Array.isArray(res.data) ? res.data.filter((row) => isUuid(String(row?.[key] ?? ""))) : [];
      return { rows, failure: null as string | null };
    };
    const [classRes, assignmentRes, paperRes, attemptRes] = await Promise.all([
      listOf<LiveClassItem>("/api/v1/classes", "id"),
      listOf<LiveAssignmentItem>("/api/v1/assignments", "id"),
      listOf<LivePaperItem>("/api/v1/assessments", "id"),
      listOf<LiveAttemptItem>("/api/v1/attempts", "attempt_id"),
    ]);
    const currentSession = getAuthSession();
    const isStaffUser = isStaffSession(currentSession);
    let loadedImports: LiveExamImportItem[] = [];
    if (isStaffUser && !fetchFn) {
      const impRes = await workspaceCall<{ items?: readonly LiveExamImportItem[] }>({
        url: "/api/v1/exam-imports?limit=5",
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      });
      if (impRes.ok && Array.isArray(impRes.data?.items)) {
        loadedImports = impRes.data.items.filter((row) => isUuid(String(row?.id ?? "")));
      }
    }
    setError(classRes.failure ?? assignmentRes.failure ?? paperRes.failure ?? attemptRes.failure);
    setClasses(classRes.rows);
    setAssignments(assignmentRes.rows);
    setPapers(paperRes.rows);
    setAttempts(attemptRes.rows);
    setExamImports(loadedImports);
    setLoading(false);
  }, [apiBase, fetchFn]);

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, load, session?.accessToken]);

  const startSitting = async (assessmentId: string, assignmentId?: string) => {
    const key = `${assessmentId}:${assignmentId ?? ""}`;
    setStartingKey(key);
    setError(null);
    const result = await workspaceCall<{ attempt_id?: string }>({
      url: "/api/v1/exam-sessions/start",
      method: "POST",
      json: assignmentId
        ? { assessment_id: assessmentId, assignment_id: assignmentId }
        : { assessment_id: assessmentId },
      fetchFn,
      apiBase,
    });
    setStartingKey(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const attemptId = result.data?.attempt_id;
    if (isUuid(attemptId)) (onOpenAttempt ?? openAttemptByDefault)(attemptId);
  };

  if (!active) return null;

  const classNameOf = (classId: string | null) => classes.find((c) => c.id === classId)?.name ?? "Lớp học";
  const openAttempts = attempts.filter((a) => a.status === "IN_PROGRESS");
  const finishedAttempts = attempts.filter((a) => a.status !== "IN_PROGRESS");

  return (
    <section
      data-testid="live-workspace-panel"
      className="mt-6 space-y-5 rounded-xl border border-emerald-200 bg-emerald-50/50 p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-extrabold text-slate-900">Dữ liệu của tôi (backend thật)</h2>
        <button
          type="button"
          data-testid="live-refresh-btn"
          onClick={() => void load()}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-50"
        >
          Tải lại
        </button>
      </div>
      {error && (
        <p role="alert" data-testid="live-workspace-error" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-900">
          {error}
        </p>
      )}
      {loading && <p className="text-xs text-slate-500">Đang tải dữ liệu...</p>}

      {classes.length > 0 && (
        <div data-testid="live-classes">
          <h3 className="text-sm font-bold text-slate-800">Lớp học</h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {classes.map((c) => (
              <li key={c.id}>
                {staff ? (
                  <Link
                    href={`/teacher/classes/${c.id}`}
                    data-testid={`live-class-${c.id}`}
                    className="inline-flex rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-semibold text-amber-900 hover:bg-amber-50"
                  >
                    {c.name} · {c.active_student_count} học sinh
                  </Link>
                ) : (
                  <span className="inline-flex rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-800">
                    {c.name}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {assignments.length > 0 && (
        <div data-testid="live-assignments">
          <h3 className="text-sm font-bold text-slate-800">Bài được giao</h3>
          <ul className="mt-2 space-y-2">
            {assignments.map((a) => (
              <li
                key={a.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"
              >
                <span>
                  <span className="font-bold text-slate-900">{a.assessment_title ?? "Đề thi"}</span> ·{" "}
                  {classNameOf(a.class_id)}
                  {a.due_at ? ` · hạn ${new Date(a.due_at).toLocaleString("vi-VN")}` : " · không giới hạn thời gian"}
                </span>
                {!staff && a.assessment_id && (
                  <button
                    type="button"
                    data-testid={`live-start-assignment-${a.id}`}
                    disabled={startingKey !== null}
                    onClick={() => void startSitting(a.assessment_id as string, a.id)}
                    className="rounded-lg bg-blue-700 px-3 py-1.5 font-semibold text-white hover:bg-blue-800 disabled:opacity-50"
                  >
                    {startingKey === `${a.assessment_id}:${a.id}` ? "Đang mở..." : "Bắt đầu / Tiếp tục làm bài"}
                  </button>
                )}
                {staff && a.class_id && (
                  <Link
                    href={`/teacher/classes/${a.class_id}`}
                    className="rounded-lg border border-amber-300 px-3 py-1.5 font-semibold text-amber-900 hover:bg-amber-50"
                  >
                    Xem bảng điểm
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {openAttempts.length > 0 && (
        <div data-testid="live-open-attempts">
          <h3 className="text-sm font-bold text-slate-800">Bài đang làm dở</h3>
          <ul className="mt-2 space-y-2">
            {openAttempts.map((a) => (
              <li
                key={a.attempt_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs"
              >
                <span>
                  <span className="font-bold text-slate-900">{a.assessment_title}</span> · hết giờ lúc{" "}
                  {new Date(a.deadline_at).toLocaleString("vi-VN")}
                </span>
                <Link
                  href={`/phong-thi/${a.attempt_id}`}
                  data-testid={`live-attempt-${a.attempt_id}`}
                  className="rounded-lg bg-blue-700 px-3 py-1.5 font-semibold text-white hover:bg-blue-800"
                >
                  Vào phòng thi
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!staff && papers.length > 0 && (
        <div data-testid="live-papers">
          <h3 className="text-sm font-bold text-slate-800">Đề đã xuất bản (luyện tự do)</h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {papers.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  data-testid={`live-start-paper-${p.id}`}
                  disabled={startingKey !== null}
                  onClick={() => void startSitting(p.id)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-50"
                >
                  {p.title}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {finishedAttempts.length > 0 && (
        <div data-testid="live-finished-attempts">
          <h3 className="text-sm font-bold text-slate-800">Bài đã nộp</h3>
          <ul className="mt-2 space-y-1 text-xs text-slate-700">
            {finishedAttempts.map((a) => (
              <li key={a.attempt_id}>
                {a.assessment_title} · {a.status}
              </li>
            ))}
          </ul>
        </div>
      )}

      {isStaffSession(session) && (
        <div
          data-testid="live-workspace-staff-tools"
          className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50/70 p-3 text-xs"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-bold text-indigo-950">Công cụ Studio cho Giáo viên / Biên soạn</span>
            <div className="flex flex-wrap gap-2">
              <Link
                href="/studio/questions"
                className="rounded-lg bg-white px-2.5 py-1 font-bold text-indigo-800 shadow-2xs hover:bg-indigo-100"
              >
                Ngân hàng câu hỏi
              </Link>
              <Link
                href="/studio/imports"
                className="rounded-lg bg-white px-2.5 py-1 font-bold text-indigo-800 shadow-2xs hover:bg-indigo-100"
              >
                Nạp file đề PDF
              </Link>
              <Link
                href="/studio/questions/new"
                className="rounded-lg bg-indigo-700 px-2.5 py-1 font-bold text-white hover:bg-indigo-800"
              >
                + Soạn câu hỏi C01
              </Link>
            </div>
          </div>
          {examImports.length > 0 && (
            <ul className="mt-2 space-y-1">
              {examImports.map((imp) => (
                <li key={imp.id} className="flex items-center justify-between gap-2 text-[11px] text-indigo-950">
                  <span className="truncate">
                    Phiên duyệt BBox: <strong>{imp.title ?? imp.id.slice(0, 8)}</strong> ({imp.status ?? "PENDING_REVIEW"})
                  </span>
                  <Link
                    href={`/studio/imports/${imp.id}/review`}
                    className="font-bold text-indigo-700 underline"
                  >
                    Mở duyệt BBox
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!loading &&
        !error &&
        classes.length + assignments.length + papers.length + attempts.length === 0 && (
          <p data-testid="live-workspace-empty" className="text-xs text-slate-600">
            Chưa có dữ liệu. Chạy <code>python -m app.ops.seed_demo</code> rồi tải lại.
          </p>
        )}
    </section>
  );
}
