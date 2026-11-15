"use client";

import React, { useEffect, useMemo, useState } from "react";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { shouldHydrateLive, shouldSyncLiveEntity, workspaceCall } from "@/lib/workspace-api";
import { evaluateOutboxQueueHealth, type CriticalAdminAction } from "@/lib/workspace6-admin-ops";
import { AdminHeader } from "./AdminHeader";
import { AdminReasonModal } from "./AdminReasonModal";
import { createDemoAdminOpsData, type AdminOpsDashboardData } from "./types";

export interface AdminOpsConsoleShellProps {
  initialData?: AdminOpsDashboardData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function AdminOpsConsoleShell({
  initialData,
  initialUserTier = "SCHOOL_ENTERPRISE",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: AdminOpsConsoleShellProps) {
  const [data, setData] = useState<AdminOpsDashboardData>(() => initialData ?? createDemoAdminOpsData());
  const [pendingAction, setPendingAction] = useState<{
    action: CriticalAdminAction;
    targetId: string;
    targetLabel: string;
    confirmLabel: string;
  } | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  useEffect(() => {
    if (!shouldHydrateLive({ hasInitialData: Boolean(initialData), apiBase })) return;
    let active = true;
    void Promise.all([
      workspaceCall<{
        pending?: number;
        dlq_count?: number;
        oldest_pending_age_seconds?: number | null;
      }>({ url: "/api/v1/admin/outbox/stats", method: "GET", fetchFn, apiBase, redirectOn401: false }),
      workspaceCall<{
        items?: Array<{
          event_id?: string;
          event_type?: string;
          attempt_count?: number;
          status?: string;
          created_at?: string;
        }>;
      }>({ url: "/api/v1/admin/dlq", method: "GET", fetchFn, apiBase, redirectOn401: false }),
      workspaceCall<{
        items?: Array<{
          key?: string;
          is_enabled?: boolean;
          rollout_percent?: number;
        }>;
      }>({ url: "/api/v1/admin/feature-flags", method: "GET", fetchFn, apiBase, redirectOn401: false }),
      workspaceCall<{
        items?: Array<{
          id?: string;
          action?: string;
          actor_id?: string | null;
          target_type?: string;
          target_id?: string;
          reason?: string | null;
          occurred_at?: string;
        }>;
      }>({ url: "/api/v1/audit-logs", method: "GET", fetchFn, apiBase, redirectOn401: false }),
    ]).then(([statsRes, dlqRes, flagsRes, auditRes]) => {
      if (!active) return;
      const firstErr = [statsRes, dlqRes, flagsRes, auditRes].find((r) => !r.ok);
      if (firstErr && !firstErr.ok && firstErr.status !== 404) {
        setApiError(firstErr.error);
        setApiErrorStatus(firstErr.status);
      }
      setData((prev) => ({
        ...prev,
        ...(statsRes.ok && statsRes.data
          ? {
              oldestQueuedAgeSeconds:
                statsRes.data.oldest_pending_age_seconds ?? prev.oldestQueuedAgeSeconds,
            }
          : {}),
        ...(dlqRes.ok && dlqRes.data?.items
          ? {
              dlqItems: dlqRes.data.items.map((it, idx) => ({
                id: String(it.event_id ?? `dlq-${idx + 1}`),
                eventType: it.event_type ?? "UNKNOWN_EVENT",
                errorCode: it.status ?? "DEAD_LETTER",
                attempts: it.attempt_count ?? 0,
                lastSeenAt: it.created_at ?? "—",
                status: it.status === "REPLAYED" ? "REPLAYED" : "FAILED_DLQ",
              })),
            }
          : {}),
        ...(flagsRes.ok && flagsRes.data?.items && flagsRes.data.items.length > 0
          ? {
              featureFlags: flagsRes.data.items.map((f) => ({
                key: String(f.key ?? ""),
                description: `Live flag (${f.key}) · ${f.rollout_percent ?? 100}%`,
                enabled: Boolean(f.is_enabled),
                pinnedVersion: `${f.rollout_percent ?? 100}%`,
              })),
            }
          : {}),
        ...(auditRes.ok && auditRes.data?.items && auditRes.data.items.length > 0
          ? {
              auditEvents: auditRes.data.items.map((a, idx) => ({
                id: String(a.id ?? `aud-${idx + 1}`),
                action: a.action ?? "AUDIT_EVENT",
                actorEmail: a.actor_id ? String(a.actor_id) : "system",
                targetRef: `${a.target_type ?? "ENTITY"}:${a.target_id ?? ""}`,
                reason: a.reason ?? "",
                createdAt: a.occurred_at ?? "—",
              })),
            }
          : {}),
      }));
    });
    return () => {
      active = false;
    };
  }, [apiBase, fetchFn, initialData]);

  const failedDlqCount = useMemo(
    () => data.dlqItems.filter((item) => item.status === "FAILED_DLQ").length,
    [data.dlqItems],
  );

  const outboxHealth = useMemo(
    () => evaluateOutboxQueueHealth(data.oldestQueuedAgeSeconds, failedDlqCount),
    [data.oldestQueuedAgeSeconds, failedDlqCount],
  );

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: Math.max(data.dlqItems.length, data.featureFlags.length),
        requiresProAsset: data.requiresProAsset,
        userTier: initialUserTier,
        previewPdfUrl: data.previewPdfUrl,
        rawAssetUrl: data.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [data, initialUserTier, initialUiOverrides, conflictDismissed],
  );

  const handleConfirmReason = async (normalizedReason: string, extras?: { totalScore?: string }) => {
    if (!pendingAction) return;
    const { action, targetId, targetLabel } = pendingAction;
    setApiError(null);
    setApiErrorStatus(null);

    if (action === "REPLAY_DLQ") {
      const syncLive = shouldSyncLiveEntity({ fetchFn, apiBase, entityIds: [targetId] });
      if (syncLive) {
        const result = await workspaceCall({
          url: `/api/v1/admin/dlq/${encodeURIComponent(targetId)}/replay`,
          method: "POST",
          json: { reason: normalizedReason },
          fetchFn,
          apiBase,
        });
        if (!result.ok) {
          setApiError(result.error);
          setApiErrorStatus(result.status);
          setPendingAction(null);
          return;
        }
      }
      setData((prev) => ({
        ...prev,
        dlqItems: prev.dlqItems.map((it) => (it.id === targetId ? { ...it, status: "REPLAYED" } : it)),
        auditEvents: [
          {
            id: `aud-${Date.now()}`,
            action: "REPLAY_DLQ",
            actorEmail: "ops@h2edu.vn",
            targetRef: targetId,
            reason: normalizedReason,
            createdAt: "Vừa xong",
          },
          ...prev.auditEvents,
        ],
      }));
      setFeedback(
        `Đã Replay DLQ "${targetLabel}" và ghi nhận audit_events${syncLive ? "" : " (mô phỏng demo cục bộ)"}.`,
      );
    } else if (action === "OVERRIDE_GRADE") {
      if (!extras?.totalScore) {
        setApiError("Thiếu total_score: backend (OverrideRequest) bắt buộc cả total_score lẫn reason.");
        setApiErrorStatus(422);
        setPendingAction(null);
        return;
      }
      const syncLive = shouldSyncLiveEntity({ fetchFn, apiBase, entityIds: [targetId] });
      if (syncLive) {
        const result = await workspaceCall({
          url: `/api/v1/grades/${encodeURIComponent(targetId)}/override`,
          method: "POST",
          json: { total_score: extras.totalScore, reason: normalizedReason },
          fetchFn,
          apiBase,
        });
        if (!result.ok) {
          setApiError(result.error);
          setApiErrorStatus(result.status);
          setPendingAction(null);
          return;
        }
      }
      setData((prev) => ({
        ...prev,
        auditEvents: [
          {
            id: `aud-${Date.now()}`,
            action: "OVERRIDE_GRADE",
            actorEmail: "ops@h2edu.vn",
            targetRef: targetId,
            reason: normalizedReason,
            createdAt: "Vừa xong",
          },
          ...prev.auditEvents,
        ],
      }));
      setFeedback(
        `Đã ghi nhận phiên phúc khảo Override Grade (${targetId}) vào audit_events${syncLive ? "" : " (mô phỏng demo cục bộ)"}.`,
      );
    }
    setPendingAction(null);
  };

  const handleToggleFlag = async (key: string) => {
    const target = data.featureFlags.find((f) => f.key === key);
    if (!target) return;
    const nextEnabled = !target.enabled;
    setApiError(null);
    setApiErrorStatus(null);
    const syncLive = shouldSyncLiveEntity({ fetchFn, apiBase });
    if (syncLive) {
      const result = await workspaceCall({
        url: `/api/v1/admin/feature-flags/${encodeURIComponent(key)}`,
        method: "PATCH",
        json: { is_enabled: nextEnabled },
        fetchFn,
        apiBase,
      });
      if (!result.ok) {
        setApiError(result.error);
        setApiErrorStatus(result.status);
        return;
      }
    }
    setData((prev) => ({
      ...prev,
      featureFlags: prev.featureFlags.map((f) => (f.key === key ? { ...f, enabled: nextEnabled } : f)),
    }));
    setFeedback(
      `Feature flag "${key}" -> ${nextEnabled ? "ENABLED" : "DISABLED"} (< 5s propagation)${syncLive ? "" : " (mô phỏng demo cục bộ)"}.`,
    );
  };

  const renderMainContent = () => {
    if (uiResolution.kind === "LoadingSkeleton") return <LoadingSkeleton title="Đang tải Admin Ops..." />;
    if (uiResolution.kind === "ErrorBoundary") {
      return <ErrorBoundary errorCode={initialUiOverrides?.fatalErrorCode ?? "ADMIN_OPS_ERROR"} errorMessage={uiResolution.bannerMessage} />;
    }
    if (uiResolution.kind === "EmptyState") {
      return <EmptyState title="Hàng đợi Outbox & DLQ trống" description="Không có sự kiện lỗi trong hệ thống." />;
    }
    if (uiResolution.kind === "LockedProPreview") {
      return <LockedProPreview previewPdfUrl={uiResolution.exposedAssetUrl} />;
    }

    return (
      <div className="mx-auto max-w-[1600px] space-y-6 px-4 py-5">
        {apiError && (
          <WorkspaceApiAlert
            message={apiError}
            status={apiErrorStatus}
            onDismiss={() => setApiError(null)}
          />
        )}
        {uiResolution.kind === "OfflineRetryBanner" && (
          <OfflineRetryBanner pendingCount={initialUiOverrides?.pendingAutosaveCount ?? 0} />
        )}

        {/* Outbox SLO Health Banner */}
        <section
          data-testid="outbox-slo-panel"
          className={`rounded-2xl border p-5 shadow-xs ${
            outboxHealth.status === "SLO_BREACH"
              ? "border-rose-300 bg-rose-50 text-rose-950"
              : "border-emerald-300 bg-emerald-50 text-emerald-950"
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider">
                G03 Transactional Outbox &amp; Inbox Latency Monitor (SLO &lt;= 90s)
              </span>
              <h1 className="mt-1 text-xl font-extrabold">
                Trạng thái Hàng đợi: {outboxHealth.status} · Tuổi job cũ nhất: {outboxHealth.oldestQueuedAgeSeconds}s ·
                DLQ chưa xử lý: {outboxHealth.dlqSize}
              </h1>
              {outboxHealth.alertCodes.length > 0 && (
                <p className="mt-1 text-xs font-semibold">
                  Cảnh báo: {outboxHealth.alertCodes.join(" · ")}
                </p>
              )}
            </div>
            <button
              type="button"
              data-testid="override-grade-audit-btn"
              onClick={() =>
                setPendingAction({
                  action: "OVERRIDE_GRADE",
                  targetId: "grade-appeal-409",
                  targetLabel: "Phiếu điểm phúc khảo C07 #grade-appeal-409",
                  confirmLabel: "Xác nhận Override Grade",
                })
              }
              className="rounded-lg border border-slate-400 bg-white px-3.5 py-2 text-xs font-bold text-slate-900 hover:bg-slate-50"
            >
              Override Grade (Cần Reason &gt;= 15 ký tự)
            </button>
          </div>

          {feedback && (
            <div
              role="status"
              data-testid="admin-ops-feedback"
              className="mt-3 rounded-lg bg-white/90 px-3 py-2 text-xs font-semibold text-slate-900"
            >
              {feedback}
            </div>
          )}

          {/* Outbox Lag Timeline Bars */}
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {data.latencySeries.map((pt) => (
              <div key={pt.label} className="rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex items-center justify-between text-[11px] font-bold text-slate-500">
                  <span>{pt.label}</span>
                  <span>DLQ: {pt.dlqCount}</span>
                </div>
                <div className="mt-1 text-base font-extrabold text-slate-900">{pt.lagSeconds}s lag</div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full ${pt.lagSeconds > 90 ? "bg-rose-600" : "bg-blue-600"}`}
                    style={{ width: `${Math.min(100, Math.round((pt.lagSeconds / 150) * 100))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* DLQ Queue & Replay */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <h2 className="text-base font-extrabold text-slate-900">
            Dead-Letter Queue (`dead_letter_items`) — Kiểm soát Replay (G03)
          </h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-bold">ID</th>
                  <th className="px-3 py-2 font-bold">Sự kiện</th>
                  <th className="px-3 py-2 font-bold">Mã lỗi</th>
                  <th className="px-3 py-2 font-bold">Thử lại</th>
                  <th className="px-3 py-2 font-bold">Trạng thái</th>
                  <th className="px-3 py-2 font-bold text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {data.dlqItems.map((item) => (
                  <tr key={item.id} className="border-b border-slate-100">
                    <td className="px-3 py-2.5 font-mono font-bold text-slate-800">{item.id}</td>
                    <td className="px-3 py-2.5 font-semibold text-slate-900">{item.eventType}</td>
                    <td className="px-3 py-2.5 font-mono text-rose-700">{item.errorCode}</td>
                    <td className="px-3 py-2.5">{item.attempts}</td>
                    <td className="px-3 py-2.5">
                      <span
                        className={`rounded px-2 py-0.5 text-[11px] font-bold ${
                          item.status === "REPLAYED"
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-rose-100 text-rose-800"
                        }`}
                      >
                        {item.status}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <button
                        type="button"
                        data-testid={`replay-dlq-btn-${item.id}`}
                        disabled={item.status === "REPLAYED"}
                        onClick={() =>
                          setPendingAction({
                            action: "REPLAY_DLQ",
                            targetId: item.id,
                            targetLabel: `${item.id} (${item.eventType})`,
                            confirmLabel: "Thực thi Replay DLQ",
                          })
                        }
                        className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-bold text-white hover:bg-slate-800 disabled:bg-slate-200 disabled:text-slate-500"
                      >
                        {item.status === "REPLAYED" ? "Đã Replay" : "Replay DLQ"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Feature Flags (G01) & Recent Audit Events (G02) */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
            <h2 className="text-sm font-extrabold text-slate-900">G01 Feature Flags &amp; Ghim phiên bản Parser/AI</h2>
            <div className="mt-3 space-y-2.5">
              {data.featureFlags.map((flag) => (
                <div
                  key={flag.key}
                  className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 text-xs"
                >
                  <div>
                    <div className="font-mono font-bold text-slate-900">{flag.key}</div>
                    <div className="text-slate-600">{flag.description}</div>
                    <div className="mt-0.5 text-[11px] text-indigo-700">Pinned: {flag.pinnedVersion}</div>
                  </div>
                  <button
                    type="button"
                    data-testid={`toggle-flag-btn-${flag.key}`}
                    onClick={() => handleToggleFlag(flag.key)}
                    className={`rounded-lg px-3 py-1.5 font-bold ${
                      flag.enabled ? "bg-emerald-700 text-white" : "bg-slate-200 text-slate-800"
                    }`}
                  >
                    {flag.enabled ? "ON" : "OFF"}
                  </button>
                </div>
              ))}
            </div>
          </section>

          <section
            data-testid="ops-audit-log-panel"
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
          >
            <h2 className="text-sm font-extrabold text-slate-900">
              G02 Nhật ký kiểm toán bất biến (`audit_events`)
            </h2>
            <ul className="mt-3 space-y-2 text-xs">
              {data.auditEvents.map((ev) => (
                <li key={ev.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center justify-between font-bold text-slate-900">
                    <span>{ev.action}</span>
                    <span className="text-[11px] text-slate-500">{ev.createdAt}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-600">
                    Actor: {ev.actorEmail} · Target: <code>{ev.targetRef}</code>
                  </div>
                  <div className="mt-1 font-medium text-slate-800">Lý do: {ev.reason}</div>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <AdminHeader activeRoute="ops" userTier={initialUserTier} />
      <main>{renderMainContent()}</main>
      {pendingAction && (
        <AdminReasonModal
          action={pendingAction.action}
          targetLabel={pendingAction.targetLabel}
          confirmLabel={pendingAction.confirmLabel}
          danger={pendingAction.action === "REPLAY_DLQ" || pendingAction.action === "OVERRIDE_GRADE"}
          requireTotalScore={pendingAction.action === "OVERRIDE_GRADE"}
          onConfirm={handleConfirmReason}
          onCancel={() => setPendingAction(null)}
        />
      )}
      {uiResolution.kind === "SequenceConflictModal" && (
        <SequenceConflictModal
          conflict={{
            questionVersionId: "admin-ops-sync",
            questionLabel: "Outbox DLQ / Feature Flag State",
            localPayload: "Thao tác điều hành cục bộ",
            serverPayload: "Trạng thái mới nhất từ máy chủ",
          }}
          onResolve={() => setConflictDismissed(true)}
        />
      )}
    </div>
  );
}
