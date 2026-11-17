"use client";

import React, { useState } from "react";
import { workspaceCall } from "@/lib/workspace-api";

export interface SourcesLiveIngestPanelProps {
  readonly fetchFn?: typeof fetch;
  readonly apiBase?: string;
}

interface CrawlerFetchResult {
  readonly source_id: string;
  readonly url: string;
  readonly http_status: number;
  readonly extracted_count: number;
  readonly created_count: number;
  readonly duplicate_count: number;
  readonly skipped: ReadonlyArray<{ readonly label: string; readonly reason: string }>;
  readonly drift_detected: boolean;
  readonly source_policy_status: string;
  readonly warnings: readonly string[];
}

interface DriveFetchResult {
  readonly connection_id: string;
  readonly folder_id: string;
  readonly listed: number;
  readonly created_question_count: number;
  readonly content_item_count: number;
  readonly files: ReadonlyArray<{
    readonly drive_file_id: string;
    readonly name: string;
    readonly kind: string;
    readonly reason: string | null;
    readonly question_ids: readonly string[];
    readonly unresolved_questions: number;
    readonly content_item_id: string | null;
  }>;
}

export function SourcesLiveIngestPanel({ fetchFn, apiBase = "" }: SourcesLiveIngestPanelProps) {
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Crawler states
  const [crawlerSourceId, setCrawlerSourceId] = useState("");
  const [newCrawlerName, setNewCrawlerName] = useState("");
  const [newCrawlerDomain, setNewCrawlerDomain] = useState("");
  const [crawlUrl, setCrawlUrl] = useState("");
  const [crawlMaxQ, setCrawlMaxQ] = useState(20);
  const [crawlRunning, setCrawlRunning] = useState(false);
  const [crawlResult, setCrawlResult] = useState<CrawlerFetchResult | null>(null);

  // Google Drive states
  const [driveConnId, setDriveConnId] = useState("");
  const [newDriveName, setNewDriveName] = useState("");
  const [newDriveVaultRef, setNewDriveVaultRef] = useState("vault://drive/public-folder");
  const [driveFolderId, setDriveFolderId] = useState("");
  const [driveMode, setDriveMode] = useState<"AUTO" | "QUESTIONS" | "DOCUMENTS">("AUTO");
  const [driveMaxFiles, setDriveMaxFiles] = useState(20);
  const [driveRunning, setDriveRunning] = useState(false);
  const [driveResult, setDriveResult] = useState<DriveFetchResult | null>(null);

  const handleRegisterCrawlerSource = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    if (!newCrawlerName.trim() || !newCrawlerDomain.trim()) {
      setErrorMsg("Nhập tên nguồn và domain hợp lệ để tạo Crawler Source.");
      return;
    }
    setCrawlRunning(true);
    const res = await workspaceCall<{ id: string }>({
      url: "/api/v1/crawler/sources",
      method: "POST",
      json: {
        name: newCrawlerName.trim(),
        base_uri: `https://${newCrawlerDomain.trim()}/`,
        allowed_domains: [newCrawlerDomain.trim()],
        allowed_path_prefixes: ["/"],
        rights_status: "LICENSED",
      },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setCrawlRunning(false);
    if (!res.ok) {
      setErrorMsg(res.error);
      return;
    }
    if (res.data?.id) setCrawlerSourceId(res.data.id);
  };

  const handleRunCrawlerFetch = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    setCrawlResult(null);
    if (!crawlerSourceId.trim() || !crawlUrl.trim()) {
      setErrorMsg("Cần nhập `source_id` (UUID) và `url` trang đích để chạy Fetch & Import.");
      return;
    }
    setCrawlRunning(true);
    const res = await workspaceCall<CrawlerFetchResult>({
      url: `/api/v1/crawler/sources/${encodeURIComponent(crawlerSourceId.trim())}/fetch-and-import`,
      method: "POST",
      json: { url: crawlUrl.trim(), max_questions: crawlMaxQ },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setCrawlRunning(false);
    if (!res.ok) {
      setErrorMsg(res.error);
      return;
    }
    if (res.data) setCrawlResult(res.data);
  };

  const handleConnectDrive = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    if (!newDriveName.trim() || !newDriveVaultRef.trim()) {
      setErrorMsg("Nhập tên kết nối và secret_vault_ref (VD: vault://... hoặc env:DRIVE_TOKEN).");
      return;
    }
    setDriveRunning(true);
    const res = await workspaceCall<{ id: string }>({
      url: "/api/v1/integrations/google-drive/connect",
      method: "POST",
      json: {
        name: newDriveName.trim(),
        secret_vault_ref: newDriveVaultRef.trim(),
        rights_status: "OWNED",
      },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setDriveRunning(false);
    if (!res.ok) {
      setErrorMsg(res.error);
      return;
    }
    if (res.data?.id) setDriveConnId(res.data.id);
  };

  const handleRunDriveFetch = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    setDriveResult(null);
    if (!driveConnId.trim() || !driveFolderId.trim()) {
      setErrorMsg("Cần nhập `connection_id` (UUID) và `folder_id` Google Drive.");
      return;
    }
    setDriveRunning(true);
    const res = await workspaceCall<DriveFetchResult>({
      url: `/api/v1/drive/connections/${encodeURIComponent(driveConnId.trim())}/fetch-and-import`,
      method: "POST",
      json: {
        folder_id: driveFolderId.trim(),
        mode: driveMode,
        max_files: driveMaxFiles,
      },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setDriveRunning(false);
    if (!res.ok) {
      setErrorMsg(res.error);
      return;
    }
    if (res.data) setDriveResult(res.data);
  };

  return (
    <section
      data-testid="sources-live-ingest-panel"
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs space-y-5"
    >
      <div>
        <span className="text-[11px] font-bold uppercase tracking-wider text-indigo-700">
          Live Acquisition · A02 Web Fetch &amp; A03 Google Drive Auto-Ingest
        </span>
        <h2 className="mt-1 text-base font-extrabold text-slate-900">
          Chạy thu thập &amp; AI giải tự động về Kho Nháp (DRAFT)
        </h2>
        <p className="mt-0.5 text-xs text-slate-600">
          Chỉ sử dụng với nguồn có bản quyền <code>OWNED / LICENSED</code>. Mọi câu hỏi đều đi qua{" "}
          <code>AiSolver</code> và vào trạng thái <code>DRAFT</code> (không tự động publish).
        </p>
      </div>

      {errorMsg && (
        <div
          role="alert"
          data-testid="sources-live-ingest-error"
          className="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs font-semibold text-rose-900"
        >
          {errorMsg}
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* A02 Crawler Live Fetch */}
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 space-y-3">
          <h3 className="text-sm font-extrabold text-slate-900">1. A02 Web Fetch &amp; Import</h3>

          <form onSubmit={handleRegisterCrawlerSource} className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <input
              type="text"
              value={newCrawlerName}
              onChange={(e) => setNewCrawlerName(e.target.value)}
              placeholder="Tên nguồn (VD: Kho Đề Mở)"
              data-testid="crawler-new-name-input"
              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
            />
            <input
              type="text"
              value={newCrawlerDomain}
              onChange={(e) => setNewCrawlerDomain(e.target.value)}
              placeholder="Domain (VD: de.example.edu.vn)"
              data-testid="crawler-new-domain-input"
              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
            />
            <button
              type="submit"
              disabled={crawlRunning}
              data-testid="crawler-register-btn"
              className="rounded-lg border border-slate-400 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 hover:bg-slate-100 disabled:opacity-50"
            >
              + Đăng ký nguồn
            </button>
          </form>

          <form onSubmit={handleRunCrawlerFetch} className="space-y-2 border-t border-slate-200 pt-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <input
                type="text"
                value={crawlerSourceId}
                onChange={(e) => setCrawlerSourceId(e.target.value)}
                placeholder="source_id (UUID)"
                data-testid="crawler-source-id-input"
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs sm:col-span-2"
              />
              <input
                type="number"
                min={1}
                max={100}
                value={crawlMaxQ}
                onChange={(e) => setCrawlMaxQ(Number(e.target.value) || 20)}
                aria-label="Số câu tối đa"
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
              />
            </div>
            <div className="flex gap-2">
              <input
                type="url"
                value={crawlUrl}
                onChange={(e) => setCrawlUrl(e.target.value)}
                placeholder="https://..."
                data-testid="crawler-fetch-url-input"
                className="flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
              />
              <button
                type="submit"
                disabled={crawlRunning}
                data-testid="crawler-fetch-import-btn"
                className="rounded-lg bg-indigo-700 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-indigo-800 disabled:opacity-50"
              >
                {crawlRunning ? "Đang quét..." : "Fetch & Import"}
              </button>
            </div>
          </form>

          {crawlResult && (
            <div
              data-testid="crawler-fetch-result"
              className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950"
            >
              <strong>HTTP {crawlResult.http_status}</strong> · Tách được {crawlResult.extracted_count} câu ·{" "}
              Tạo mới: <strong>{crawlResult.created_count}</strong> · Trùng: {crawlResult.duplicate_count} · Trạng thái:{" "}
              <code>{crawlResult.source_policy_status}</code>
            </div>
          )}
        </div>

        {/* A03 Google Drive Auto-Ingest */}
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 space-y-3">
          <h3 className="text-sm font-extrabold text-slate-900">2. A03 Google Drive Auto-Ingest</h3>

          <form onSubmit={handleConnectDrive} className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <input
              type="text"
              value={newDriveName}
              onChange={(e) => setNewDriveName(e.target.value)}
              placeholder="Tên kết nối Drive"
              data-testid="drive-new-name-input"
              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
            />
            <input
              type="text"
              value={newDriveVaultRef}
              onChange={(e) => setNewDriveVaultRef(e.target.value)}
              placeholder="vault://... hoặc env:VAR"
              data-testid="drive-vault-ref-input"
              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs"
            />
            <button
              type="submit"
              disabled={driveRunning}
              data-testid="drive-connect-btn"
              className="rounded-lg border border-slate-400 bg-white px-3 py-1.5 text-xs font-bold text-slate-800 hover:bg-slate-100 disabled:opacity-50"
            >
              + Kết nối Drive
            </button>
          </form>

          <form onSubmit={handleRunDriveFetch} className="space-y-2 border-t border-slate-200 pt-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <input
                type="text"
                value={driveConnId}
                onChange={(e) => setDriveConnId(e.target.value)}
                placeholder="connection_id (UUID)"
                data-testid="drive-conn-id-input"
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs sm:col-span-2"
              />
              <select
                value={driveMode}
                onChange={(e) => setDriveMode(e.target.value as "AUTO" | "QUESTIONS" | "DOCUMENTS")}
                data-testid="drive-mode-select"
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold"
              >
                <option value="AUTO">AUTO</option>
                <option value="QUESTIONS">QUESTIONS</option>
                <option value="DOCUMENTS">DOCUMENTS</option>
              </select>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={driveFolderId}
                onChange={(e) => setDriveFolderId(e.target.value)}
                placeholder="Google Drive folder_id"
                data-testid="drive-folder-id-input"
                className="flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs"
              />
              <input
                type="number"
                min={1}
                max={50}
                value={driveMaxFiles}
                onChange={(e) => setDriveMaxFiles(Number(e.target.value) || 20)}
                aria-label="Số file tối đa"
                className="w-16 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs"
              />
              <button
                type="submit"
                disabled={driveRunning}
                data-testid="drive-fetch-import-btn"
                className="rounded-lg bg-blue-700 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-blue-800 disabled:opacity-50"
              >
                {driveRunning ? "Đang đồng bộ..." : "Sync Drive"}
              </button>
            </div>
          </form>

          {driveResult && (
            <div
              data-testid="drive-fetch-result"
              className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950 space-y-1"
            >
              <div>
                Folder <code>{driveResult.folder_id}</code> · Tìm thấy {driveResult.listed} file · Câu hỏi tạo mới:{" "}
                <strong>{driveResult.created_question_count}</strong> · Tài liệu:{" "}
                <strong>{driveResult.content_item_count}</strong>
              </div>
              {driveResult.files.length > 0 && (
                <ul className="list-disc pl-4 text-[11px]">
                  {driveResult.files.map((f) => (
                    <li key={f.drive_file_id}>
                      {f.name} → <code>{f.kind}</code>
                      {f.reason ? ` (${f.reason})` : ` (${f.question_ids.length} câu)`}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
