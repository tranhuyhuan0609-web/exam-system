"use client";

import Link from "next/link";
import React from "react";
import type { UserTier } from "@/lib/ui-state-machine";

export interface AdminHeaderProps {
  activeRoute: "ops" | "sources" | "takedowns";
  userTier: UserTier;
}

export function AdminHeader({ activeRoute, userTier }: AdminHeaderProps) {
  const navItems: readonly {
    readonly key: AdminHeaderProps["activeRoute"];
    readonly href: string;
    readonly label: string;
  }[] = [
    { key: "ops", href: "/admin/ops", label: "Outbox & DLQ Ops (G01/G03)" },
    { key: "sources", href: "/admin/sources", label: "Drive Picker & Crawler (A02/A03)" },
    { key: "takedowns", href: "/admin/takedowns", label: "Legal Takedowns (G02)" },
  ];

  return (
    <header
      data-testid="h2edu-admin-header"
      className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur"
    >
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-slate-900 text-sm font-extrabold tracking-tight text-white shadow-xs">
              H2
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-extrabold tracking-tight text-slate-900">H2Edu Admin Console</span>
                <span className="hidden rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700 sm:inline-block">
                  Workspace 6
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                Vận hành Hàng đợi Outbox, Google Picker (drive.file), Parser Drift &amp; Gỡ bản quyền khẩn cấp
              </p>
            </div>
          </Link>
        </div>

        <nav aria-label="Điều hướng Workspace 6 Quản trị" className="flex flex-wrap items-center gap-1.5">
          {navItems.map((item) => {
            const active = activeRoute === item.key;
            return (
              <Link
                key={item.key}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
                  active
                    ? "bg-slate-900 text-white shadow-xs"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
          <Link
            href="/teacher/exam-builder"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Teacher LMS
          </Link>
        </nav>

        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-800">
          Vai trò: OPS_ADMIN ({userTier})
        </span>
      </div>
    </header>
  );
}
