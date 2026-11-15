"use client";

import React, { useMemo, useState } from "react";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import type { ReviewerRightsStatus as RightsStatus } from "@/lib/bbox-reviewer";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { workspaceCall } from "@/lib/workspace-api";
import { evaluateDrivePairingCandidate, evaluateParserDriftStatus } from "@/lib/workspace6-admin-ops";
import { AdminHeader } from "./AdminHeader";
import { AdminReasonModal } from "./AdminReasonModal";
import { createDemoAdminSourcesData, type AdminSourcesConsoleData } from "./types";

export interface AdminSourcesShellProps {
  initialData?: AdminSourcesConsoleData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function AdminSourcesShell({
  initialData,
  initialUserTier = "SCHOOL_ENTERPRISE",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: AdminSourcesShellProps) {
  const [data, setData] = useState<AdminSourcesConsoleData>(() => initialData ?? createDemoAdminSourcesData());
  const [pickerOpen, setPickerOpen] = useState(false);
  const [rightsModalTarget, setRightsModalTarget] = useState<{
    sourceId: string;
    sourceName: string;
    nextRights: RightsStatus;
  } | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [conflictDismissed, setConflictDismissed] = useState(false);

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: Math.max(data.crawlerSources.length, data.pairingCandidates.length),
        requiresProAsset: data.requiresProAsset,
        userTier: initialUserTier,
        previewPdfUrl: data.previewPdfUrl,
        rawAssetUrl: data.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [data, initialUserTier, initialUiOverrides, conflictDismissed],
  );

  const handlePickSampleDriveFiles = () => {
    setData((prev) => ({
      ...prev,
      pickedFileNames: [
        ...prev.pickedFileNames,
        `De_thi_Chuyen_Toan_Picked_${prev.pickedFileNames.length + 1}.pdf`,
      ],
    }));
    setPickerOpen(false);
    setFeedback("Google Picker (scope drive.file) đã nạp thêm file vào hàng đợi A03 đồng bộ vi sai.");
  };

  const handleConfirmPairing = async (pairId: string) => {
    setApiError(null);
    setApiErrorStatus(null);
    const result = await workspaceCall({
      url: `/api/v1/drive/pairings/${encodeURIComponent(pairId)}/confirm`,
      method: "POST",
      json: { confirmed: true },
      fetchFn,
      apiBase,
    });
    if (!result.ok) {
      setApiError(result.error);
      setApiErrorStatus(result.status);
      return;
    }
    setData((prev) => ({
      ...prev,
      pairingCandidates: prev.pairingCandidates.map((p) =>
        p.id === pairId ? { ...p, status: "CONFIRMED" } : p,
      ),
    }));
    setFeedback(`Đã xác nhận ghép cặp Đề + HDC cho "${pairId}".`);
  };

  const handleConfirmRightsChange = async (normalizedReason: string) => {
    if (!rightsModalTarget) return;
    const { sourceId, sourceName, nextRights } = rightsModalTarget;
    setApiError(null);
    setApiErrorStatus(null);
    const result = await workspaceCall({
      url: "/api/v1/rights-records",
      method: "POST",
      json: {
        source_id: sourceId,
        rights_status: nextRights,
        reason: normalizedReason,
      },
      fetchFn,
      apiBase,
    });
    if (!result.ok) {
      setApiError(result.error);
      setApiErrorStatus(result.status);
      setRightsModalTarget(null);
      return;
    }
    setData((prev) => ({
      ...prev,
      crawlerSources: prev.crawlerSources.map((s) =>
        s.id === sourceId ? { ...s, rightsStatus: nextRights } : s,
      ),
      auditEvents: [
        {
          id: `aud-rights-${Date.now()}`,
          action: "CHANGE_RIGHTS_STATUS",
          actorEmail: "ops@h2edu.vn",
          targetRef: sourceId,
          reason: normalizedReason,
          createdAt: "Vừa xong",
        },
        ...prev.auditEvents,
      ],
    }));
    setRightsModalTarget(null);
    setFeedback(`Đã cập nhật bản quyền "${sourceName}" -> ${nextRights} và ghi audit_events.`);
  };

  const renderMainContent = () => {
    if (uiResolution.kind === "LoadingSkeleton") return <LoadingSkeleton title="Đang tải Sources Console..." />;
    if (uiResolution.kind === "ErrorBoundary") {
      return <ErrorBoundary errorCode={initialUiOverrides?.fatalErrorCode ?? "SOURCES_ERROR"} errorMessage={uiResolution.bannerMessage} />;
    }
    if (uiResolution.kind === "EmptyState") {
      return <EmptyState title="Chưa cấu hình nguồn dữ liệu" description="Kết nối Google Picker hoặc Crawler nguồn hợp pháp." />;
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

        {/* Google Drive Picker & Service Account B2B Guide */}
        <section
          data-testid="google-drive-picker-panel"
          className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
        >
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider text-blue-700">
                A03 Google Drive Sync · Scope `drive.file` + Google Picker API (Non-Sensitive)
              </span>
              <h1 className="mt-1 text-xl font-extrabold text-slate-900">
                Quản lý Nguồn Drive Trường học &amp; Ghép cặp Đề + HDC (4 tín hiệu)
              </h1>
              <p className="mt-1 text-xs text-slate-600">
                Chia sẻ thư mục Drive của trường/tổ chuyên môn cho Service Account:{" "}
                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-bold text-slate-900">
                  {data.serviceAccountEmail}
                </code>
              </p>
            </div>
            <button
              type="button"
              data-testid="open-google-picker-btn"
              onClick={() => setPickerOpen((o) => !o)}
              className="rounded-xl bg-blue-700 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-blue-800"
            >
              Mở Google Picker (drive.file / .zip)
            </button>
          </div>

          {feedback && (
            <div
              role="status"
              data-testid="admin-sources-feedback"
              className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-900"
            >
              {feedback}
            </div>
          )}

          {pickerOpen && (
            <div
              data-testid="google-picker-drawer"
              className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-4 text-xs"
            >
              <div className="font-bold text-blue-950">Google Picker Sandbox (Scope: drive.file)</div>
              <p className="mt-1 text-blue-900">
                Chọn nhiều file Đề + HDC hoặc gói `.zip` (giải nén an toàn A03 chặn Zip Bomb / Path Traversal `../`).
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  data-testid="confirm-picker-selection-btn"
                  onClick={handlePickSampleDriveFiles}
                  className="rounded-lg bg-blue-700 px-3 py-1.5 font-bold text-white"
                >
                  Chọn &amp; Nhập vào hàng đợi Sync
                </button>
                <button
                  type="button"
                  onClick={() => setPickerOpen(false)}
                  className="rounded-lg border border-blue-300 bg-white px-3 py-1.5 font-semibold text-blue-900"
                >
                  Đóng Picker
                </button>
              </div>
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {data.pickedFileNames.map((fn) => (
              <span
                key={fn}
                className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 font-mono text-[11px] font-semibold text-slate-700"
              >
                {fn}
              </span>
            ))}
          </div>

          {/* 4-signal Pairing Table */}
          <div className="mt-5 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-bold">File Đề + File HDC</th>
                  <th className="px-3 py-2 font-bold">Thư mục</th>
                  <th className="px-3 py-2 font-bold">Số câu (Đề / HDC)</th>
                  <th className="px-3 py-2 font-bold">Tin cậy 4 tín hiệu</th>
                  <th className="px-3 py-2 font-bold text-right">Xác nhận ghép</th>
                </tr>
              </thead>
              <tbody>
                {data.pairingCandidates.map((pair) => {
                  const evalRes = evaluateDrivePairingCandidate({
                    filenameSimilarity: pair.filenameSimilarity,
                    sameFolder: pair.sameFolder,
                    titleOverlap: pair.titleOverlap,
                    examQuestionCount: pair.examQuestionCount,
                    keyQuestionCount: pair.keyQuestionCount,
                  });
                  return (
                    <tr key={pair.id} className="border-b border-slate-100">
                      <td className="px-3 py-2.5">
                        <div className="font-bold text-slate-900">{pair.examFileName}</div>
                        <div className="text-[11px] text-slate-600">+ {pair.keyFileName}</div>
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-slate-600">{pair.folderName}</td>
                      <td className="px-3 py-2.5 font-bold">
                        {pair.examQuestionCount} / {pair.keyQuestionCount}
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className={`rounded px-2 py-0.5 text-[11px] font-bold ${
                            evalRes.highConfidence
                              ? "bg-emerald-100 text-emerald-800"
                              : "bg-amber-100 text-amber-900"
                          }`}
                        >
                          {evalRes.confidencePct}%
                        </span>
                        {evalRes.warningCodes.length > 0 && (
                          <div className="mt-1 text-[10px] font-semibold text-rose-700">
                            {evalRes.warningCodes.join(", ")}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <button
                          type="button"
                          data-testid={`confirm-pairing-btn-${pair.id}`}
                          disabled={!evalRes.canConfirm || pair.status === "CONFIRMED"}
                          onClick={() => handleConfirmPairing(pair.id)}
                          className="rounded-lg bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-800 disabled:bg-slate-200 disabled:text-slate-500"
                        >
                          {pair.status === "CONFIRMED" ? "Đã ghép cặp" : "Confirm"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        {/* Crawler Sources & ParserDriftMonitor */}
        <section
          data-testid="parser-drift-monitor-panel"
          className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs"
        >
          <h2 className="text-base font-extrabold text-slate-900">
            A02 Permitted Web Crawler &amp; `ParserDriftMonitor`
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Tự động chuyển <code>PAUSED_DRIFT</code> khi tỷ lệ bóc tách rỗng &gt; 15% trên chuỗi 30+ trang liên tiếp.
          </p>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                  <th className="px-3 py-2 font-bold">Nguồn Crawler</th>
                  <th className="px-3 py-2 font-bold">Rights Status</th>
                  <th className="px-3 py-2 font-bold">Parser Drift (Empty/Pages)</th>
                  <th className="px-3 py-2 font-bold text-right">Quản trị Bản quyền (Audit)</th>
                </tr>
              </thead>
              <tbody>
                {data.crawlerSources.map((src) => {
                  const drift = evaluateParserDriftStatus(src.emptyPageCount, src.consecutivePages);
                  return (
                    <tr key={src.id} className="border-b border-slate-100">
                      <td className="px-3 py-2.5">
                        <div className="font-bold text-slate-900">{src.name}</div>
                        <div className="font-mono text-[11px] text-slate-500">{src.baseUrl}</div>
                      </td>
                      <td className="px-3 py-2.5 font-bold text-slate-800">{src.rightsStatus}</td>
                      <td className="px-3 py-2.5">
                        <span
                          data-testid={`drift-status-${src.id}`}
                          className={`rounded px-2 py-0.5 text-[11px] font-bold ${
                            drift.status === "PAUSED_DRIFT"
                              ? "bg-rose-100 text-rose-800"
                              : drift.status === "WATCH"
                                ? "bg-amber-100 text-amber-900"
                                : "bg-emerald-100 text-emerald-800"
                          }`}
                        >
                          {drift.status} ({drift.emptyRatePct}% · {src.emptyPageCount}/{src.consecutivePages})
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <button
                          type="button"
                          data-testid={`change-rights-btn-${src.id}`}
                          onClick={() =>
                            setRightsModalTarget({
                              sourceId: src.id,
                              sourceName: src.name,
                              nextRights: src.rightsStatus === "LICENSED" ? "RESTRICTED" : "LICENSED",
                            })
                          }
                          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 hover:bg-slate-50"
                        >
                          Change Rights Status
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <AdminHeader activeRoute="sources" userTier={initialUserTier} />
      <main>{renderMainContent()}</main>
      {rightsModalTarget && (
        <AdminReasonModal
          action="CHANGE_RIGHTS_STATUS"
          targetLabel={`${rightsModalTarget.sourceName} -> ${rightsModalTarget.nextRights}`}
          confirmLabel="Xác nhận đổi Rights Status"
          danger={rightsModalTarget.nextRights === "RESTRICTED"}
          onConfirm={handleConfirmRightsChange}
          onCancel={() => setRightsModalTarget(null)}
        />
      )}
      {uiResolution.kind === "SequenceConflictModal" && (
        <SequenceConflictModal
          conflict={{
            questionVersionId: "admin-sources-sync",
            questionLabel: "Google Drive Pairing / Crawler Source",
            localPayload: "Cập nhật đang chỉnh sửa cục bộ",
            serverPayload: "Phiên làm việc mới hơn trên máy chủ",
          }}
          onResolve={() => setConflictDismissed(true)}
        />
      )}
    </div>
  );
}
