import React from "react";
import { AuthStatusBadge } from "@/components/auth/AuthStatusBadge";
import { HomeDirectorySections } from "@/components/auth/HomeDirectorySections";
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
        <HomeDirectorySections />
      </div>
    </main>
  );
}
