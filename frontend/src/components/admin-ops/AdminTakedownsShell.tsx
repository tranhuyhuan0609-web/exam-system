"use client";

import React, { useMemo, useState } from "react";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { AdminHeader } from "./AdminHeader";
import { AdminReasonModal } from "./AdminReasonModal";
import { createDemoAdminTakedownsData, type AdminTakedownsConsoleData, type TakedownCaseRecord } from "./types";

export interface AdminTakedownsShellProps {
  initialData?: AdminTakedownsConsoleData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function AdminTakedownsShell({
  initialData,
  initialUserTier = "SCHOOL_ENTERPRISE",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: AdminTakedownsShellProps) {
  const [data, setData] = useState<AdminTakedownsConsoleData>(
    () => initialData ?? createDemoAdminTakedownsData(),
  );
  const [newTargetTitle, setNewTargetTitle] = useState("");
  const [newTargetId, setNewTargetId] = useState("");
  const [newLegalRef, setNewLegalRef] = useState("");
  const [pendingTakedown, setPendingTakedown] = useState<TakedownCaseRecord | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: data.cases.length,
        requiresProAsset: data.requiresProAsset,
        userTier: initialUserTier,
        previewPdfUrl: data.previewPdfUrl,
        rawAssetUrl: data.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [data, initialUserTier, initialUiOverrides, conflictDismissed],
  );

  const handleCreateCase = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTargetTitle.trim() || !newLegalRef.trim()) return;
    const caseId = `td-2026-${data.cases.length + 1}`;
    const created: TakedownCaseRecord = {
      id: caseId,
      targetType: "CONTENT_ITEM",
      targetId: newTargetId.trim() || `ci-${Date.now()}`,
      targetTitle: newTargetTitle.trim(),
      currentRightsStatus: "LICENSED",
      claimantName: "Yêu cầu gỡ bản quyền mới",
      legalNoticeRef: newLegalRef.trim(),
      status: "OPEN",
    };
    const caller = fetchFn ?? (typeof fetch === "function" ? fetch : null);
    if (caller) {
      try {
        await caller(`${apiBase}/api/v1/takedown-cases`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(created),
        });
      } catch {
        // Fallback offline/demo preservation
      }
    }
    setData((prev) => ({ ...prev, cases: [created, ...prev.cases] }));
    setNewTargetTitle("");
    setNewTargetId("");
    setNewLegalRef("");
    setFeedback(`Đã mở hồ sơ pháp lý "${caseId}". Vui lòng nhấn Execute Legal Takedown để gỡ khẩn cấp.`);
  };

  const handleConfirmExecuteTakedown = async (normalizedReason: string) => {
    if (!pendingTakedown) return;
    const targetCase = pendingTakedown;
    const caller = fetchFn ?? (typeof fetch === "function" ? fetch : null);
    if (caller) {
      try {
        await caller(`${apiBase}/api/v1/takedown-cases/${encodeURIComponent(targetCase.id)}/execute`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: normalizedReason }),
        });
      } catch {
        // Demo update preserved cleanly
      }
    }
    setData((prev) => ({
      ...prev,
      cases: prev.cases.map((c) =>
        c.id === targetCase.id
          ? {
              ...c,
              status: "EXECUTED",
              currentRightsStatus: "TAKEDOWN",
              executedReason: normalizedReason,
            }
          : c,
      ),
      auditEvents: [
        {
          id: `aud-td-${Date.now()}`,
          action: "EXECUTE_TAKEDOWN",
          actorEmail: "legal-admin@h2edu.vn",
          targetRef: `${targetCase.targetType}:${targetCase.targetId}`,
          reason: normalizedReason,
          createdAt: "Vừa xong",
        },
        ...prev.auditEvents,
      ],
    }));
    setFeedback(
      `Đã thực thi Execute Legal Takedown cho "${targetCase.targetTitle}" -> rights_status=TAKEDOWN, phát sự kiện rights.takedown.executed (< 30s).`,
    );
    setPendingTakedown(null);
  };

  const renderMainContent = () => {
    if (uiResolution.kind === "LoadingSkeleton") return <LoadingSkeleton title="Đang tải Takedown Console..." />;
    if (uiResolution.kind === "ErrorBoundary") {
      return <ErrorBoundary errorCode={initialUiOverrides?.fatalErrorCode ?? "TAKEDOWN_ERROR"} errorMessage={uiResolution.bannerMessage} />;
    }
    if (uiResolution.kind === "EmptyState") {
      return <EmptyState title="Không có hồ sơ gỡ bản quyền" description="Hệ thống hiện chưa có yêu cầu takedown nào." />;
    }
    if (uiResolution.kind === "LockedProPreview") {
      return <LockedProPreview previewPdfUrl={uiResolution.exposedAssetUrl} />;
    }

    return (
      <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-5">
        {uiResolution.kind === "OfflineRetryBanner" && (
          <OfflineRetryBanner pendingCount={initialUiOverrides?.pendingAutosaveCount ?? 0} />
        )}

        <section className="rounded-2xl border border-rose-200 bg-white p-5 shadow-xs">
          <span className="text-[11px] font-bold uppercase tracking-wider text-rose-700">
            G02 Immutable Audit Trail &amp; Emergency Legal Takedown (&lt; 30s CDN/Search Purge)
          </span>
          <h1 className="mt-1 text-xl font-extrabold text-slate-900">
            Bảng điều khiển Gỡ Bản Quyền Khẩn Cấp (`takedown_cases`)
          </h1>
          <p className="mt-1 text-xs text-slate-600">
            Khi bấm <strong>Execute Legal Takedown</strong>, bắt buộc nhập lý do kiểm toán tối thiểu{" "}
            <strong>15 ký tự</strong> để ghi bất biến vào <code>audit_events</code> và gỡ học liệu khỏi B07 Search /
            C04 Exam Assembly.
          </p>

          {feedback && (
            <div
              role="status"
              data-testid="takedowns-feedback"
              className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-900"
            >
              {feedback}
            </div>
          )}

          {/* Create Case Form */}
          <form onSubmit={handleCreateCase} className="mt-4 grid grid-cols-1 gap-3 rounded-xl bg-slate-50 p-4 md:grid-cols-4">
            <input
              type="text"
              placeholder="Tiêu đề học liệu / câu hỏi cần gỡ..."
              value={newTargetTitle}
              onChange={(e) => setNewTargetTitle(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs md:col-span-2"
            />
            <input
              type="text"
              placeholder="Số công văn pháp lý (VD: DMCA-2026-09)..."
              value={newLegalRef}
              onChange={(e) => setNewLegalRef(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs"
            />
            <button
              type="submit"
              className="rounded-lg bg-slate-900 px-4 py-2 text-xs font-bold text-white hover:bg-slate-800"
            >
              Mở hồ sơ Takedown
            </button>
          </form>
        </section>

        {/* Cases List */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <h2 className="text-base font-extrabold text-slate-900">Danh sách hồ sơ Takedown</h2>
          <div className="mt-4 space-y-3">
            {data.cases.map((tc) => (
              <div
                key={tc.id}
                data-testid={`takedown-case-card-${tc.id}`}
                className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-slate-200 p-4"
              >
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-700">{tc.id}</span>
                    <span
                      data-testid={`takedown-rights-status-${tc.id}`}
                      className={`rounded px-2 py-0.5 text-[11px] font-bold ${
                        tc.currentRightsStatus === "TAKEDOWN"
                          ? "bg-rose-700 text-white"
                          : "bg-amber-100 text-amber-900"
                      }`}
                    >
                      {tc.currentRightsStatus}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700">
                      {tc.legalNoticeRef}
                    </span>
                  </div>
                  <div className="mt-1 text-sm font-bold text-slate-900">{tc.targetTitle}</div>
                  <div className="text-xs text-slate-600">
                    Bên khiếu nại: {tc.claimantName} · {tc.targetType} ({tc.targetId})
                  </div>
                  {tc.executedReason && (
                    <div className="mt-1 text-xs font-semibold text-rose-800">
                      Biên bản audit: {tc.executedReason}
                    </div>
                  )}
                </div>

                <div>
                  {tc.status === "EXECUTED" ? (
                    <span
                      data-testid={`takedown-executed-badge-${tc.id}`}
                      className="inline-flex items-center rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-bold text-white"
                    >
                      Đã thực thi Takedown
                    </span>
                  ) : (
                    <button
                      type="button"
                      data-testid={`execute-takedown-btn-${tc.id}`}
                      onClick={() => setPendingTakedown(tc)}
                      className="rounded-lg bg-rose-700 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-rose-800"
                    >
                      Execute Legal Takedown
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Audit Events Feed */}
        <section
          data-testid="takedowns-audit-feed"
          className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
        >
          <h2 className="text-sm font-extrabold text-slate-900">Biên bản pháp lý (`audit_events`)</h2>
          <ul className="mt-3 space-y-2 text-xs">
            {data.auditEvents.map((ev) => (
              <li key={ev.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="flex items-center justify-between font-bold text-slate-900">
                  <span>{ev.action}</span>
                  <span className="text-[11px] text-slate-500">{ev.createdAt}</span>
                </div>
                <div className="mt-0.5 text-[11px] text-slate-600">Target: {ev.targetRef}</div>
                <div className="mt-1 font-medium text-slate-800">Lý do: {ev.reason}</div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <AdminHeader activeRoute="takedowns" userTier={initialUserTier} />
      <main>{renderMainContent()}</main>
      {pendingTakedown && (
        <AdminReasonModal
          action="EXECUTE_TAKEDOWN"
          targetLabel={`${pendingTakedown.id} · ${pendingTakedown.targetTitle}`}
          confirmLabel="Execute Legal Takedown"
          danger
          onConfirm={handleConfirmExecuteTakedown}
          onCancel={() => setPendingTakedown(null)}
        />
      )}
      {uiResolution.kind === "SequenceConflictModal" && (
        <SequenceConflictModal
          conflict={{
            questionVersionId: "admin-takedowns-sync",
            questionLabel: "Legal Takedown Audit Ledger",
            localPayload: "Hồ sơ đang thao tác cục bộ",
            serverPayload: "Phiên bản kiểm toán mới hơn",
          }}
          onResolve={() => setConflictDismissed(true)}
        />
      )}
    </div>
  );
}
