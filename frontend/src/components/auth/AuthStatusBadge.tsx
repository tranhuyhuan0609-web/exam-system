"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import {
  AUTH_CHANGED_EVENT,
  AUTH_STORAGE_KEY,
  clearAuthSession,
  getAuthSession,
  type AuthSession,
} from "@/lib/auth-client";

export interface AuthStatusBadgeProps {
  compact?: boolean;
  onSessionChange?: (session: AuthSession | null) => void;
}

export function AuthStatusBadge({ compact = true, onSessionChange }: AuthStatusBadgeProps) {
  const [session, setSession] = useState<AuthSession | null>(null);

  useEffect(() => {
    const sync = () => {
      const current = getAuthSession();
      setSession(current);
      onSessionChange?.(current);
    };
    sync();

    const handleStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === AUTH_STORAGE_KEY) {
        sync();
      }
    };

    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
      window.removeEventListener("storage", handleStorage);
    };
  }, [onSessionChange]);

  const handleLogout = () => {
    clearAuthSession();
    setSession(null);
    onSessionChange?.(null);
  };

  if (!session) {
    return (
      <div
        data-testid="auth-status-badge"
        className={`flex items-center gap-2 ${
          compact
            ? ""
            : "rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2"
        }`}
      >
        <span className="hidden rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 md:inline-block">
          Khách / Demo
        </span>
        <Link
          href="/dang-nhap"
          data-testid="auth-login-link"
          className="rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-800 transition hover:bg-blue-100"
        >
          Đăng nhập
        </Link>
      </div>
    );
  }

  const primaryRole = session.roles[0] ?? "USER";
  const label = session.displayName || session.email || session.userId;

  return (
    <div
      data-testid="auth-status-badge"
      className={`flex flex-wrap items-center gap-2 ${
        compact ? "" : "rounded-xl border border-emerald-200 bg-emerald-50/70 px-3.5 py-2"
      }`}
    >
      <span
        data-testid="auth-session-pill"
        title={`User: ${session.userId} | Tenant: ${session.tenantId}${
          session.membershipId ? ` | Membership: ${session.membershipId}` : ""
        }`}
        className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-900"
      >
        <span className="h-2 w-2 rounded-full bg-emerald-600" aria-hidden="true" />
        <span>{primaryRole}</span>
        {!compact && <span className="text-emerald-700">· {label}</span>}
        <span className="hidden text-emerald-700 sm:inline">· {session.tenantId}</span>
      </span>
      <button
        type="button"
        data-testid="auth-logout-btn"
        onClick={handleLogout}
        className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-100"
      >
        Đăng xuất
      </button>
    </div>
  );
}
