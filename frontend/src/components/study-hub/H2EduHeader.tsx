"use client";

import Link from "next/link";
import React from "react";
import type { UserTier } from "@/lib/ui-state-machine";
import { H2EDU_BRAND } from "./types";

export interface H2EduHeaderProps {
  activeRoute: "hoc-lieu" | "lo-trinh" | "so-tay-cau-sai";
  userTier: UserTier;
  onTierChange?: (tier: UserTier) => void;
  showTierSwitcher?: boolean;
}

export function H2EduHeader({
  activeRoute,
  userTier,
  onTierChange,
  showTierSwitcher = true,
}: H2EduHeaderProps) {
  const navItems: {
    readonly key: "hoc-lieu" | "lo-trinh" | "so-tay-cau-sai";
    readonly href: string;
    readonly label: string;
  }[] = [
    { key: "hoc-lieu", href: "/hoc-lieu/cong-thuc-bayes", label: "Học liệu 4 cấp" },
    { key: "lo-trinh", href: "/lo-trinh", label: "Lộ trình học" },
    { key: "so-tay-cau-sai", href: "/so-tay-cau-sai", label: "Sổ tay câu sai" },
  ];

  return (
    <header
      data-testid="h2edu-study-header"
      className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur"
    >
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-blue-700 text-sm font-extrabold tracking-tight text-white shadow-xs">
              H2
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-extrabold tracking-tight text-slate-900">
                  {H2EDU_BRAND.name}
                </span>
                <span className="hidden rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700 sm:inline-block">
                  {H2EDU_BRAND.slogan}
                </span>
              </div>
              <p className="text-[11px] text-slate-500">Workspace 1 · Learner Study &amp; Practice Hub</p>
            </div>
          </Link>
        </div>

        <nav aria-label="Điều hướng học tập H2Edu" className="flex flex-wrap items-center gap-1.5">
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
            href="/phong-thi/demo-attempt?demo=1"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-800"
          >
            Phòng thi
          </Link>
        </nav>

        <div className="flex items-center gap-2">
          <span
            data-testid="h2edu-tier-badge"
            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
              userTier === "FREE"
                ? "bg-slate-100 text-slate-700"
                : "bg-amber-100 text-amber-900"
            }`}
          >
            Gói: {userTier}
          </span>
          {showTierSwitcher && onTierChange && (
            <button
              type="button"
              data-testid="toggle-tier-btn"
              onClick={() => onTierChange(userTier === "FREE" ? "STUDENT_PRO" : "FREE")}
              className="rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-900 transition hover:bg-amber-100"
            >
              {userTier === "FREE" ? "Thử góc nhìn PRO" : "Về góc nhìn FREE"}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
