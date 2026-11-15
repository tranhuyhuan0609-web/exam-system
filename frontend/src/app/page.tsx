import React from "react";
import Link from "next/link";
import { AuthStatusBadge } from "@/components/auth/AuthStatusBadge";
import { LiveWorkspacePanel } from "@/components/auth/LiveWorkspacePanel";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col justify-center px-6 py-12">
      <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
            H2Edu • Học thông minh - Thi bứt phá
          </div>
          <AuthStatusBadge compact={false} />
        </div>
        <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900">
          Cổng học liệu &amp; Luyện thi THPTQG 2025
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          Hệ thống hợp nhất 6 không gian làm việc: Học tập &amp; Phòng thi Học sinh (WS1–WS2),
          Studio Ngân hàng Câu hỏi · Nạp PDF &amp; Duyệt BBox (WS3–WS4), Giáo viên LMS (WS5) và Quản trị Ops (WS6).
        </p>

        <LiveWorkspacePanel />

        <div className="mt-7 space-y-6">
          <section aria-label="Không gian Học sinh">
            <h2 className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-slate-500">
              1. Không gian Học sinh (Workspace 1 – 2)
            </h2>
            <div className="flex flex-wrap gap-2.5">
              <Link
                href="/hoc-lieu/cong-thuc-bayes"
                className="inline-flex items-center rounded-lg bg-indigo-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-800"
              >
                Mở Học liệu Bayes (Workspace 1)
              </Link>
              <Link
                href="/lo-trinh"
                className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition hover:bg-slate-50"
              >
                Lộ trình học
              </Link>
              <Link
                href="/so-tay-cau-sai"
                className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition hover:bg-slate-50"
              >
                Sổ tay câu sai
              </Link>
              <Link
                href="/phong-thi/demo-attempt?demo=1"
                className="inline-flex items-center rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-800"
              >
                Mở Phòng thi mẫu (Workspace 2)
              </Link>
            </div>
          </section>

          <section aria-label="Không gian Giáo viên và Studio">
            <h2 className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-slate-500">
              2. Không gian Giáo viên, Ngân hàng đề &amp; Studio (Workspace 3 – 5)
            </h2>
            <div className="flex flex-wrap gap-2.5">
              <Link
                href="/studio/questions"
                className="inline-flex items-center rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-800"
              >
                Ngân hàng Câu hỏi C01
              </Link>
              <Link
                href="/studio/imports"
                className="inline-flex items-center rounded-lg bg-sky-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-sky-800"
              >
                Nạp file đề PDF &amp; Hàng đợi
              </Link>
              <Link
                href="/studio/imports/demo-run/review"
                className="inline-flex items-center rounded-lg border border-indigo-300 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-800 transition hover:bg-indigo-100"
              >
                Duyệt BBox đề nhập (Workspace 3)
              </Link>
              <Link
                href="/studio/content/cong-thuc-bayes"
                className="inline-flex items-center rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-800 transition hover:bg-emerald-100"
              >
                Block Studio (Workspace 4)
              </Link>
              <Link
                href="/studio/questions/new"
                className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition hover:bg-slate-50"
              >
                Soạn câu hỏi C01
              </Link>
              <Link
                href="/teacher/exam-builder"
                className="inline-flex items-center rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-900 transition hover:bg-amber-100"
              >
                Ma trận Đề THPTQG 2025 (Workspace 5)
              </Link>
              <Link
                href="/teacher/classes/class-12a1"
                className="inline-flex items-center rounded-lg border border-amber-300 bg-white px-4 py-2 text-sm font-semibold text-amber-900 transition hover:bg-amber-50"
              >
                Lớp học 12A1 LMS (Workspace 5)
              </Link>
            </div>
          </section>

          <section aria-label="Quản trị Hệ thống">
            <h2 className="mb-2.5 text-xs font-extrabold uppercase tracking-wider text-slate-500">
              3. Quản trị Vận hành &amp; Nguồn dữ liệu (Workspace 6)
            </h2>
            <div className="flex flex-wrap gap-2.5">
              <Link
                href="/admin/ops"
                className="inline-flex items-center rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                Admin Ops &amp; SLO (Workspace 6)
              </Link>
              <Link
                href="/admin/sources"
                className="inline-flex items-center rounded-lg border border-slate-300 bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-800 transition hover:bg-slate-200"
              >
                Drive Pairing &amp; Drift (Workspace 6)
              </Link>
              <Link
                href="/admin/takedowns"
                className="inline-flex items-center rounded-lg border border-rose-300 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-900 transition hover:bg-rose-100"
              >
                DMCA &amp; Takedowns (Workspace 6)
              </Link>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
