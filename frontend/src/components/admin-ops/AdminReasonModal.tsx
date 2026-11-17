"use client";

import React, { useMemo, useState } from "react";
import {
  MAX_OVERRIDE_TOTAL_SCORE,
  MIN_ADMIN_AUDIT_REASON_CHARS,
  validateAdminMutationReason,
  validateOverrideTotalScore,
  type CriticalAdminAction,
} from "@/lib/workspace6-admin-ops";

export interface AdminReasonModalProps {
  action: CriticalAdminAction;
  targetLabel: string;
  confirmLabel: string;
  danger?: boolean;
  /** Ask for the `total_score` of a manual grade override (0..1000, 2 decimals) next to the reason. */
  requireTotalScore?: boolean;
  onConfirm: (normalizedReason: string, extras?: { totalScore?: string }) => void;
  onCancel: () => void;
}

const ACTION_TITLES: Record<CriticalAdminAction, string> = {
  EXECUTE_TAKEDOWN: "Xác nhận Thực thi Gỡ Bản Quyền Khẩn Cấp (Execute Legal Takedown)",
  REPLAY_DLQ: "Xác nhận Phát Lại Sự Kiện DLQ (Replay DLQ)",
  CHANGE_RIGHTS_STATUS: "Xác nhận Thay Đổi Trạng Thái Bản Quyền (Change Rights Status)",
  OVERRIDE_GRADE: "Xác nhận Ghi Đè Điểm Số Thủ Công (Override Grade)",
};

export function AdminReasonModal({
  action,
  targetLabel,
  confirmLabel,
  danger = true,
  requireTotalScore = false,
  onConfirm,
  onCancel,
}: AdminReasonModalProps) {
  const [rawReason, setRawReason] = useState("");
  const validation = useMemo(() => validateAdminMutationReason(action, rawReason), [action, rawReason]);
  const [rawScore, setRawScore] = useState("");
  const scoreValidation = useMemo(() => validateOverrideTotalScore(rawScore), [rawScore]);
  const canConfirm = validation.ok && (!requireTotalScore || scoreValidation.ok);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="admin-reason-modal-title"
      data-testid="admin-reason-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4"
    >
      <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-6 shadow-xl">
        <div className="text-[11px] font-bold uppercase tracking-wider text-rose-700">
          G02 Immutable Audit Enforcement · Minimum {MIN_ADMIN_AUDIT_REASON_CHARS} chars
        </div>
        <h2 id="admin-reason-modal-title" className="mt-1 text-base font-extrabold text-slate-900">
          {ACTION_TITLES[action]}
        </h2>
        <p className="mt-1 text-xs text-slate-600">
          Đối tượng tác động: <strong className="text-slate-900">{targetLabel}</strong>. Lý do bắt buộc được ghi vào{" "}
          <code>audit_events</code> (bảng append-only không thể xóa sửa).
        </p>

        {requireTotalScore && (
          <div className="mt-4">
            <label htmlFor="admin-override-score-input" className="block text-xs font-bold text-slate-800">
              Tổng điểm mới (0 – {MAX_OVERRIDE_TOTAL_SCORE}, tối đa 2 chữ số thập phân):
            </label>
            <input
              id="admin-override-score-input"
              data-testid="admin-override-score-input"
              type="text"
              inputMode="decimal"
              value={rawScore}
              onChange={(e) => setRawScore(e.target.value)}
              placeholder="Ví dụ: 7.25"
              className="mt-1.5 w-40 rounded-xl border border-slate-300 p-2 text-xs text-slate-900 outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-300"
            />
            <span
              data-testid="admin-override-score-msg"
              className={`ml-2 text-[11px] font-semibold ${scoreValidation.ok ? "text-emerald-700" : "text-rose-700"}`}
            >
              {scoreValidation.errorCode === "SCORE_REQUIRED"
                ? "Bắt buộc nhập tổng điểm."
                : scoreValidation.errorCode === "SCORE_NOT_A_NUMBER"
                  ? "Tổng điểm phải là số."
                  : scoreValidation.errorCode === "SCORE_TOO_MANY_DECIMALS"
                    ? "Tối đa 2 chữ số thập phân."
                    : scoreValidation.errorCode === "SCORE_OUT_OF_RANGE"
                      ? `Tổng điểm phải trong khoảng 0 – ${MAX_OVERRIDE_TOTAL_SCORE}.`
                      : "Tổng điểm hợp lệ."}
            </span>
          </div>
        )}

        <div className="mt-4">
          <label htmlFor="admin-audit-reason-input" className="block text-xs font-bold text-slate-800">
            Lý do kiểm toán (tối thiểu {MIN_ADMIN_AUDIT_REASON_CHARS} ký tự hiển thị):
          </label>
          <textarea
            id="admin-audit-reason-input"
            data-testid="admin-reason-input"
            rows={3}
            value={rawReason}
            onChange={(e) => setRawReason(e.target.value)}
            placeholder="Ví dụ: Tiếp nhận công văn yêu cầu gỡ DMCA-VN-2026-1114-01 từ pháp chế NXB..."
            className="mt-1.5 w-full rounded-xl border border-slate-300 p-3 text-xs text-slate-900 outline-none focus:border-slate-900 focus:ring-2 focus:ring-slate-300"
          />
          <div className="mt-1.5 flex items-center justify-between text-[11px]">
            <span
              data-testid="admin-reason-validation-msg"
              className={validation.ok ? "font-semibold text-emerald-700" : "font-semibold text-rose-700"}
            >
              {validation.errorCode === "REASON_REQUIRED"
                ? "Bắt buộc nhập lý do trước khi thực thi."
                : validation.errorCode === "REASON_TOO_SHORT"
                  ? `Cần tối thiểu ${MIN_ADMIN_AUDIT_REASON_CHARS} ký tự hợp lệ (${validation.charLength}/${MIN_ADMIN_AUDIT_REASON_CHARS}).`
                  : "Lý do hợp lệ — sẵn sàng ghi audit_events."}
            </span>
            <span className="font-mono text-slate-500">
              {validation.charLength}/{MIN_ADMIN_AUDIT_REASON_CHARS}+
            </span>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="admin-reason-cancel-btn"
            onClick={onCancel}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
          >
            Hủy
          </button>
          <button
            type="button"
            data-testid="admin-reason-confirm-btn"
            disabled={!canConfirm}
            onClick={() =>
              onConfirm(
                validation.normalizedReason,
                requireTotalScore ? { totalScore: scoreValidation.normalized } : undefined,
              )
            }
            className={`rounded-lg px-4 py-2 text-xs font-bold text-white transition disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600 ${
              danger ? "bg-rose-700 hover:bg-rose-800" : "bg-slate-900 hover:bg-slate-800"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
