"use client";

import Link from "next/link";
import React from "react";
import type { UserTier } from "@/lib/ui-state-machine";

export interface StudioHeaderProps {
  activeRoute: "ws3-review" | "ws4-content" | "ws4-question";
  userTier: UserTier;
  onTierChange?: (tier: UserTier) => void;
  showTierSwitcher?: boolean;
}

export function StudioHeader({
  activeRoute,
  userTier,
  onTierChange,
  showTierSwitcher = true,
}: StudioHeaderProps) {
  const navItems: readonly {
    readonly key: StudioHeaderProps["activeRoute"];
    readonly href: string;
    readonly label: string;
  }[] = [
    { key: "ws3-review", href: "/studio/imports/demo-run/review", label: "Workspace 3 · Duyệt BBox" },
    { key: "ws4-content", href: "/studio/content/cong-thuc-bayes", label: "Workspace 4 · Block Studio" },
    { key: "ws4-question", href: "/studio/questions/new", label: "Soạn câu hỏi C01" },
  ];

  return (
    <header
      data-testid="h2edu-studio-header"
      className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur"
    >
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-700 text-sm font-extrabold tracking-tight text-white shadow-xs">
              H2
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-extrabold tracking-tight text-slate-900">H2Edu Studio</span>
                <span className="hidden rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-700 sm:inline-block">
                  Học thông minh - Thi bứt phá
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                {activeRoute === "ws3-review"
                  ? "Workspace 3 · Side-by-Side Bounding-Box & SymPy Reviewer"
                  : "Workspace 4 · VietJack Block Studio & Question Authoring"}
              </p>
            </div>
          </Link>
        </div>

        <nav aria-label="Điều hướng H2Edu Studio" className="flex flex-wrap items-center gap-1.5">
          {navItems.map((item) => {
            const active = activeRoute === item.key;
            return (
              <Link
                key={item.key}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
                  active
                    ? "bg-indigo-700 text-white shadow-xs"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
          <Link
            href="/hoc-lieu/cong-thuc-bayes"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Học liệu
          </Link>
        </nav>

        <div className="flex items-center gap-2">
          <span
            data-testid="studio-tier-badge"
            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
              userTier === "FREE" ? "bg-slate-100 text-slate-700" : "bg-indigo-100 text-indigo-900"
            }`}
          >
            Quyền Studio: {userTier}
          </span>
          {showTierSwitcher && onTierChange && (
            <button
              type="button"
              data-testid="toggle-studio-tier-btn"
              onClick={() => onTierChange(userTier === "FREE" ? "TEACHER_PRO" : "FREE")}
              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-100"
            >
              {userTier === "FREE" ? "Mở góc nhìn TEACHER_PRO" : "Thử góc nhìn FREE"}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
