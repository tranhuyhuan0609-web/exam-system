"use client";

import Link from "next/link";
import React from "react";
import { AuthStatusBadge } from "@/components/auth/AuthStatusBadge";
import type { UserTier } from "@/lib/ui-state-machine";

export interface TeacherHeaderProps {
  activeRoute: "exam-builder" | "class-lms";
  classId?: string;
  userTier: UserTier;
  onTierChange?: (tier: UserTier) => void;
  showTierSwitcher?: boolean;
}

export function TeacherHeader({
  activeRoute,
  classId = "class-12a1",
  userTier,
  onTierChange,
  showTierSwitcher = true,
}: TeacherHeaderProps) {
  const navItems: readonly {
    readonly key: TeacherHeaderProps["activeRoute"];
    readonly href: string;
    readonly label: string;
  }[] = [
    { key: "exam-builder", href: "/teacher/exam-builder", label: "Matrix Exam Builder (C03/C04)" },
    { key: "class-lms", href: `/teacher/classes/${classId}`, label: "Quản lý Lớp & Sổ điểm (F01–F03)" },
  ];

  return (
    <header
      data-testid="h2edu-teacher-header"
      className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur"
    >
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-blue-700 text-sm font-extrabold tracking-tight text-white shadow-xs">
              H2
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-extrabold tracking-tight text-slate-900">H2Edu Teacher LMS</span>
                <span className="hidden rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700 sm:inline-block">
                  Workspace 5
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                {activeRoute === "exam-builder"
                  ? "Thiết kế Ma trận Đề thi THPTQG 2025+ & Báo cáo thiếu câu"
                  : "Bảng điểm lớp, Phân tích phương án nhiễu & Teacher AI Copilot (E06)"}
              </p>
            </div>
          </Link>
        </div>

        <nav aria-label="Điều hướng Workspace 5 Giáo viên" className="flex flex-wrap items-center gap-1.5">
          {navItems.map((item) => {
            const active = activeRoute === item.key;
            return (
              <Link
                key={item.key}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
                  active
                    ? "bg-blue-700 text-white shadow-xs"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
          <Link
            href="/studio/imports/demo-run/review"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Studio
          </Link>
          <Link
            href="/admin/ops"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Admin Ops
          </Link>
        </nav>

        <div className="flex items-center gap-2">
          <span
            data-testid="teacher-tier-badge"
            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
              userTier === "FREE" ? "bg-slate-100 text-slate-700" : "bg-blue-100 text-blue-900"
            }`}
          >
            Gói GV: {userTier}
          </span>
          {showTierSwitcher && onTierChange && (
            <button
              type="button"
              data-testid="toggle-teacher-tier-btn"
              onClick={() => onTierChange(userTier === "FREE" ? "TEACHER_PRO" : "FREE")}
              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-100"
            >
              {userTier === "FREE" ? "Chuyển TEACHER_PRO" : "Thử FREE"}
            </button>
          )}
          <AuthStatusBadge />
        </div>
      </div>
    </header>
  );
}
