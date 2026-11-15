"use client";

import Link from "next/link";
import React from "react";

export interface WorkspaceApiAlertProps {
  message: string | null;
  status?: number | null;
  onDismiss?: () => void;
}

export function WorkspaceApiAlert({ message, status, onDismiss }: WorkspaceApiAlertProps) {
  if (!message) return null;

  const isAuthError = status === 401;
  const isConflictOrRate = status === 409 || status === 429;
  const toneClass = isConflictOrRate
    ? "border-amber-300 bg-amber-50 text-amber-950"
    : "border-rose-300 bg-rose-50 text-rose-950";

  return (
    <div
      role="alert"
      data-testid="workspace-api-error"
      className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs font-medium shadow-xs ${toneClass}`}
    >
      <div className="min-w-0 flex-1">
        <span className="font-bold">Lỗi đồng bộ API: </span>
        <span>{message}</span>
      </div>
      <div className="flex items-center gap-2">
        {isAuthError && (
          <Link
            href="/dang-nhap"
            className="rounded-lg bg-rose-700 px-2.5 py-1 text-[11px] font-bold text-white hover:bg-rose-800"
          >
            Đăng nhập lại
          </Link>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-lg border border-current/25 px-2 py-0.5 text-[11px] font-semibold hover:bg-black/5"
          >
            Đóng
          </button>
        )}
      </div>
    </div>
  );
}
