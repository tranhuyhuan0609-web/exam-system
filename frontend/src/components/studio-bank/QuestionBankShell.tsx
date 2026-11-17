"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { SafeKatexText } from "@/components/workspace/SafeKatexText";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  AUTH_CHANGED_EVENT,
  getAuthSession,
  hasAuthToken,
  isDemoModeClient,
  type AuthSession,
} from "@/lib/auth-client";
import type { UserTier } from "@/lib/ui-state-machine";
import { shouldHydrateLive, workspaceCall } from "@/lib/workspace-api";

export interface QuestionBankOption {
  readonly key?: string;
  readonly text?: string;
}

export interface QuestionBankListItem {
  readonly id: string;
  readonly tenant_id: string;
  readonly visibility_scope: string;
  readonly stable_code: string;
  readonly item_type: string;
  readonly primary_taxonomy_node_id: string | null;
  readonly curriculum_node_id: string | null;
  readonly exam_framework_tags: readonly string[];
  readonly difficulty_level: string;
  readonly current_version_id: string | null;
  readonly acquisition_status: string;
  readonly verification_status: string;
  readonly rights_status: string;
  readonly publication_status: string;
  readonly access_tier: string;
  readonly created_at: string;
  readonly version_id: string;
  readonly version_no: number;
  readonly is_immutable: boolean;
  readonly stimulus_id: string | null;
  readonly stem_text: string;
  readonly options: readonly QuestionBankOption[];
}

export interface QuestionVersionSummary {
  readonly id: string;
  readonly version_no: number;
  readonly is_immutable: boolean;
  readonly is_current: boolean;
  readonly change_reason: string;
  readonly created_by: string | null;
  readonly created_at: string;
}

export interface QuestionVersionDetail {
  readonly id: string;
  readonly question_id: string;
  readonly version_no: number;
  readonly stem: { readonly text?: string };
  readonly options: readonly QuestionBankOption[];
  readonly is_immutable: boolean;
  readonly content_hash: string;
  readonly change_reason: string;
  readonly created_at: string;
}

export interface QuestionDetailPayload {
  readonly question: Omit<
    QuestionBankListItem,
    "version_id" | "version_no" | "is_immutable" | "stimulus_id" | "stem_text" | "options"
  >;
  readonly shown_version: QuestionVersionDetail | null;
  readonly versions: readonly QuestionVersionSummary[];
}

export interface TaxonomyNodeOption {
  readonly id: string;
  readonly code: string;
  readonly name: string;
  readonly node_type: string;
  readonly code_path: string;
}

interface QuestionListApiResponse {
  readonly items: readonly QuestionBankListItem[];
  readonly next_cursor: string | null;
  readonly has_more: boolean;
  readonly limit: number;
}

interface TaxonomyListApiResponse {
  readonly items: readonly TaxonomyNodeOption[];
}

const ITEM_TYPE_LABELS: Readonly<Record<string, string>> = {
  SINGLE_CHOICE: "Trắc nghiệm (P.I)",
  MULTIPLE_SELECT: "Nhiều lựa chọn",
  CLUSTER_TRUE_FALSE: "Đúng / Sai (P.II)",
  SHORT_NUMERIC: "Trả lời ngắn (P.III)",
  ESSAY: "Tự luận",
  GROUP_STEM: "Chùm câu hỏi",
};

const DIFFICULTY_LABELS: Readonly<Record<string, string>> = {
  NB: "Nhận biết (NB)",
  TH: "Thông hiểu (TH)",
  VD: "Vận dụng (VD)",
  VDC: "Vận dụng cao (VDC)",
};

const PUBLICATION_BADGE_CLASS: Readonly<Record<string, string>> = {
  PUBLISHED: "bg-emerald-100 text-emerald-900 border-emerald-200",
  DRAFT: "bg-amber-100 text-amber-900 border-amber-200",
  ARCHIVED: "bg-slate-200 text-slate-700 border-slate-300",
};

const DEMO_TAXONOMY: readonly TaxonomyNodeOption[] = [
  {
    id: "demo-tax-bayes",
    code: "TOAN12_XAC_SUAT_BAYES",
    name: "Xác suất có điều kiện & Công thức Bayes",
    node_type: "TOPIC",
    code_path: "TOAN.12.XAC_SUAT.BAYES",
  },
];

const DEMO_QUESTIONS: readonly QuestionBankListItem[] = [
  {
    id: "demo-q-bayes-1",
    tenant_id: "demo-tenant",
    visibility_scope: "TENANT_PRIVATE",
    stable_code: "DEMO_C01_MCQ_001",
    item_type: "SINGLE_CHOICE",
    primary_taxonomy_node_id: "demo-tax-bayes",
    curriculum_node_id: null,
    exam_framework_tags: ["THPTQG_2025"],
    difficulty_level: "TH",
    current_version_id: "demo-v-1",
    acquisition_status: "AUTHORED",
    verification_status: "UNVERIFIED",
    rights_status: "OWNED",
    publication_status: "DRAFT",
    access_tier: "FREE",
    created_at: "2026-11-15T00:00:00Z",
    version_id: "demo-v-1",
    version_no: 1,
    is_immutable: false,
    stimulus_id: null,
    stem_text: "Cho $P(A)=0.4$, $P(B|A)=0.7$ và $P(B|\\overline{A})=0.2$. Tính $P(A|B)$.",
    options: [
      { key: "A", text: "$\\frac{7}{10}$" },
      { key: "B", text: "$\\frac{1}{2}$" },
      { key: "C", text: "$\\frac{3}{5}$" },
      { key: "D", text: "$\\frac{2}{5}$" },
    ],
  },
];

export interface QuestionBankShellProps {
  initialItems?: readonly QuestionBankListItem[];
  initialTaxonomy?: readonly TaxonomyNodeOption[];
  initialUserTier?: UserTier;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function QuestionBankShell({
  initialItems,
  initialTaxonomy,
  initialUserTier = "TEACHER_PRO",
  fetchFn,
  apiBase = "",
}: QuestionBankShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);

  const [items, setItems] = useState<readonly QuestionBankListItem[]>(() => initialItems ?? []);
  const [taxonomyNodes, setTaxonomyNodes] = useState<readonly TaxonomyNodeOption[]>(
    () => initialTaxonomy ?? [],
  );
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const [searchInput, setSearchInput] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [itemType, setItemType] = useState("");
  const [difficultyLevel, setDifficultyLevel] = useState("");
  const [publicationStatus, setPublicationStatus] = useState("");
  const [taxonomyNodeId, setTaxonomyNodeId] = useState("");

  const [loadingList, setLoadingList] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedQuestionId, setSelectedQuestionId] = useState<string | null>(null);
  const [detail, setDetail] = useState<QuestionDetailPayload | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const [forkReason, setForkReason] = useState("Cập nhật diễn đạt câu hỏi");
  const [forkStemText, setForkStemText] = useState("");
  const [forking, setForking] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);

  const taxonomyById = useMemo(() => {
    const map = new Map<string, TaxonomyNodeOption>();
    for (const node of taxonomyNodes) {
      map.set(node.id, node);
    }
    return map;
  }, [taxonomyNodes]);

  useEffect(() => {
    const syncAuth = () => {
      setSession(getAuthSession());
      setHasToken(hasAuthToken());
      setDemoMode(isDemoModeClient());
      setAuthChecked(true);
    };
    syncAuth();
    window.addEventListener(AUTH_CHANGED_EVENT, syncAuth);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, syncAuth);
  }, []);

  const loadTaxonomy = useCallback(async () => {
    if (!shouldHydrateLive({ hasInitialData: Boolean(initialTaxonomy), apiBase })) {
      if (isDemoModeClient() && !initialTaxonomy) {
        setTaxonomyNodes(DEMO_TAXONOMY);
      }
      return;
    }
    const res = await workspaceCall<TaxonomyListApiResponse>({
      url: "/api/v1/taxonomy/nodes?limit=100",
      method: "GET",
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    if (res.ok && Array.isArray(res.data?.items)) {
      setTaxonomyNodes(res.data.items);
    }
  }, [apiBase, fetchFn, initialTaxonomy]);

  const buildQuestionsUrl = useCallback(
    (cursor?: string | null) => {
      const params = new URLSearchParams();
      params.set("limit", "20");
      if (appliedSearch.trim()) params.set("q", appliedSearch.trim());
      if (itemType) params.set("item_type", itemType);
      if (difficultyLevel) params.set("difficulty_level", difficultyLevel);
      if (publicationStatus) params.set("publication_status", publicationStatus);
      if (taxonomyNodeId) params.set("taxonomy_node_id", taxonomyNodeId);
      if (cursor) params.set("cursor", cursor);
      return `/api/v1/questions?${params.toString()}`;
    },
    [appliedSearch, difficultyLevel, itemType, publicationStatus, taxonomyNodeId],
  );

  const loadQuestions = useCallback(
    async (cursor?: string | null, append = false) => {
      if (initialItems) return;
      const isDemo = isDemoModeClient();
      const canLive = shouldHydrateLive({ hasInitialData: false, apiBase });
      if (!canLive) {
        if (isDemo) {
          setItems(DEMO_QUESTIONS);
          setNextCursor(null);
          setHasMore(false);
        } else {
          setItems([]);
          setNextCursor(null);
          setHasMore(false);
        }
        return;
      }

      if (append) {
        setLoadingMore(true);
      } else {
        setLoadingList(true);
      }
      setApiError(null);
      setApiErrorStatus(null);

      const res = await workspaceCall<QuestionListApiResponse>({
        url: buildQuestionsUrl(cursor),
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      });

      if (append) {
        setLoadingMore(false);
      } else {
        setLoadingList(false);
      }

      if (!res.ok) {
        setApiError(res.error);
        setApiErrorStatus(res.status);
        return;
      }

      const incoming = Array.isArray(res.data?.items) ? res.data.items : [];
      setItems((prev) => (append ? [...prev, ...incoming] : incoming));
      setNextCursor(res.data?.next_cursor ?? null);
      setHasMore(Boolean(res.data?.has_more));
    },
    [apiBase, buildQuestionsUrl, fetchFn, initialItems],
  );

  useEffect(() => {
    if (!authChecked) return;
    void loadTaxonomy();
  }, [authChecked, loadTaxonomy, hasToken]);

  useEffect(() => {
    if (!authChecked) return;
    void loadQuestions(null, false);
  }, [authChecked, loadQuestions, hasToken]);

  const handleSelectQuestion = useCallback(
    async (item: QuestionBankListItem) => {
      setSelectedQuestionId(item.id);
      setFeedback(null);
      setApiError(null);
      setApiErrorStatus(null);

      if (isDemoModeClient() || !hasAuthToken()) {
        setDetail({
          question: item,
          shown_version: {
            id: item.version_id,
            question_id: item.id,
            version_no: item.version_no,
            stem: { text: item.stem_text },
            options: item.options,
            is_immutable: item.is_immutable,
            content_hash: "demo-hash",
            change_reason: "Bản xem thử",
            created_at: item.created_at,
          },
          versions: [
            {
              id: item.version_id,
              version_no: item.version_no,
              is_immutable: item.is_immutable,
              is_current: true,
              change_reason: "Bản xem thử",
              created_by: null,
              created_at: item.created_at,
            },
          ],
        });
        setForkStemText(item.stem_text);
        return;
      }

      setLoadingDetail(true);
      const res = await workspaceCall<QuestionDetailPayload>({
        url: `/api/v1/questions/${item.id}`,
        method: "GET",
        fetchFn,
        apiBase,
        redirectOn401: false,
      });
      setLoadingDetail(false);

      if (!res.ok) {
        setApiError(res.error);
        setApiErrorStatus(res.status);
        return;
      }

      if (!res.data) {
        setApiError("Phản hồi API chi tiết câu hỏi trống.");
        return;
      }

      setDetail(res.data);
      setForkStemText(res.data.shown_version?.stem?.text ?? item.stem_text);
    },
    [apiBase, fetchFn],
  );

  const handleCreateVersionFork = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selectedQuestionId) return;
    if (!forkReason.trim()) {
      setApiError("Vui lòng nhập lý do tạo phiên bản nháp mới.");
      setApiErrorStatus(422);
      return;
    }

    setForking(true);
    setFeedback(null);
    setApiError(null);
    setApiErrorStatus(null);

    const res = await workspaceCall<QuestionVersionDetail>({
      url: `/api/v1/questions/${selectedQuestionId}/versions`,
      method: "POST",
      json: {
        change_reason: forkReason.trim(),
        ...(forkStemText.trim() ? { stem_text: forkStemText.trim() } : {}),
      },
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    setForking(false);

    if (!res.ok) {
      setApiError(res.error);
      setApiErrorStatus(res.status);
      return;
    }

    setFeedback(`Đã tạo phiên bản nháp v${res.data?.version_no ?? "mới"} cho câu hỏi.`);
    await loadQuestions(null, false);
    const refreshed = await workspaceCall<QuestionDetailPayload>({
      url: `/api/v1/questions/${selectedQuestionId}`,
      method: "GET",
      fetchFn,
      apiBase,
      redirectOn401: false,
    });
    if (refreshed.ok && refreshed.data) {
      setDetail(refreshed.data);
      setForkStemText(refreshed.data.shown_version?.stem?.text ?? forkStemText);
    }
  };

  const handleSearchSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setAppliedSearch(searchInput);
  };

  const handleResetFilters = () => {
    setSearchInput("");
    setAppliedSearch("");
    setItemType("");
    setDifficultyLevel("");
    setPublicationStatus("");
    setTaxonomyNodeId("");
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <StudioHeader
        activeRoute="ws4-question-bank"
        userTier={userTier}
        onTierChange={setUserTier}
      />

      <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-extrabold tracking-tight text-slate-900">
                Ngân hàng Câu hỏi C01
              </h1>
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-[11px] font-bold text-emerald-800">
                Zero Answer Leak (Không lộ đáp án)
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-600">
              Tra cứu câu hỏi của tenant & kho công khai đã xuất bản (RLS an toàn), xem lịch sử phiên bản và rẽ nhánh bản nháp.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/studio/imports"
              className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50"
            >
              Nạp đề PDF & Duyệt BBox
            </Link>
            <Link
              href="/studio/questions/new"
              className="rounded-xl bg-indigo-700 px-3.5 py-2 text-xs font-bold text-white shadow-xs hover:bg-indigo-800"
            >
              + Soạn câu hỏi mới (C01)
            </Link>
          </div>
        </div>

        <WorkspaceApiAlert
          message={apiError}
          status={apiErrorStatus}
          onDismiss={() => {
            setApiError(null);
            setApiErrorStatus(null);
          }}
        />

        {feedback && (
          <div
            data-testid="question-bank-feedback"
            className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs font-semibold text-emerald-900"
          >
            {feedback}
          </div>
        )}

        {!hasToken && !demoMode && !initialItems && (
          <section
            data-testid="question-bank-auth-prompt"
            className="rounded-2xl border border-amber-200 bg-amber-50/80 p-5 text-sm text-amber-950"
          >
            <p className="font-bold">Cần đăng nhập tài khoản biên soạn để xem Ngân hàng Câu hỏi live</p>
            <p className="mt-1 text-xs text-amber-900">
              API <code>GET /api/v1/questions</code> yêu cầu vai trò{" "}
              <code>TEACHER / EDITOR / TENANT_ADMIN</code> (học sinh sẽ nhận HTTP 403). Đăng nhập nhanh tài khoản{" "}
              <code>teacher@h2edu.demo</code> tại trang đăng nhập để tải dữ liệu PostgreSQL thật.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link
                href="/dang-nhap"
                className="rounded-xl bg-amber-700 px-3.5 py-2 text-xs font-bold text-white hover:bg-amber-800"
              >
                Đi tới Đăng nhập
              </Link>
              <Link
                href="/studio/questions?demo=1"
                className="rounded-xl border border-amber-300 bg-white px-3.5 py-2 text-xs font-bold text-amber-900 hover:bg-amber-100/60"
              >
                Xem dữ liệu mẫu (?demo=1)
              </Link>
            </div>
          </section>
        )}

        <section
          aria-label="Bộ lọc Ngân hàng câu hỏi"
          className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs"
        >
          <form onSubmit={handleSearchSubmit} className="grid gap-3 md:grid-cols-6">
            <div className="md:col-span-2">
              <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                Tìm mã hoặc nội dung câu
              </label>
              <input
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="VD: DEMO_C01, Bayes, mol..."
                data-testid="qb-search-input"
                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                Dạng câu hỏi
              </label>
              <select
                value={itemType}
                onChange={(e) => setItemType(e.target.value)}
                data-testid="qb-item-type-select"
                className="w-full rounded-xl border border-slate-300 bg-white px-2.5 py-2 text-xs"
              >
                <option value="">Tất cả dạng</option>
                {Object.entries(ITEM_TYPE_LABELS).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                Mức độ (Bloom)
              </label>
              <select
                value={difficultyLevel}
                onChange={(e) => setDifficultyLevel(e.target.value)}
                data-testid="qb-difficulty-select"
                className="w-full rounded-xl border border-slate-300 bg-white px-2.5 py-2 text-xs"
              >
                <option value="">Tất cả mức độ</option>
                {Object.entries(DIFFICULTY_LABELS).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                Trạng thái xuất bản
              </label>
              <select
                value={publicationStatus}
                onChange={(e) => setPublicationStatus(e.target.value)}
                data-testid="qb-status-select"
                className="w-full rounded-xl border border-slate-300 bg-white px-2.5 py-2 text-xs"
              >
                <option value="">Tất cả trạng thái</option>
                <option value="DRAFT">Bản nháp (DRAFT)</option>
                <option value="PUBLISHED">Đã xuất bản (PUBLISHED)</option>
                <option value="ARCHIVED">Lưu trữ (ARCHIVED)</option>
              </select>
            </div>

            <div>
              <label className="mb-1 block text-[11px] font-bold uppercase tracking-wide text-slate-600">
                Chủ đề (Taxonomy)
              </label>
              <select
                value={taxonomyNodeId}
                onChange={(e) => setTaxonomyNodeId(e.target.value)}
                data-testid="qb-taxonomy-select"
                className="w-full rounded-xl border border-slate-300 bg-white px-2.5 py-2 text-xs"
              >
                <option value="">Mọi chủ đề</option>
                {taxonomyNodes.map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.code} — {node.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-wrap items-center gap-2 md:col-span-6">
              <button
                type="submit"
                data-testid="qb-apply-filter-btn"
                className="rounded-xl bg-indigo-700 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-indigo-800"
              >
                Áp dụng lọc
              </button>
              <button
                type="button"
                onClick={handleResetFilters}
                className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                Xóa bộ lọc
              </button>
              <span className="ml-auto text-xs text-slate-500">
                {session
                  ? `Đang xem với quyền ${session.roles.join(", ")}`
                  : demoMode
                    ? "Chế độ Demo cục bộ"
                    : "Chưa đăng nhập"}
              </span>
            </div>
          </form>
        </section>

        <div className="grid gap-5 lg:grid-cols-12">
          <section className="space-y-3 lg:col-span-7" aria-label="Danh sách câu hỏi">
            {loadingList ? (
              <div className="rounded-2xl border border-slate-200 bg-white p-6 text-xs text-slate-600">
                Đang tải danh sách câu hỏi từ API...
              </div>
            ) : items.length === 0 ? (
              <div
                data-testid="qb-empty-state"
                className="rounded-2xl border border-slate-200 bg-white p-6 text-xs text-slate-600"
              >
                Chưa có câu hỏi phù hợp với bộ lọc hiện tại. Hãy xóa bộ lọc hoặc tạo câu hỏi mới ở{" "}
                <Link href="/studio/questions/new" className="font-bold text-indigo-700 underline">
                  Soạn câu hỏi C01
                </Link>
                .
              </div>
            ) : (
              items.map((item) => {
                const selected = selectedQuestionId === item.id;
                const taxNode = item.primary_taxonomy_node_id
                  ? taxonomyById.get(item.primary_taxonomy_node_id)
                  : undefined;
                const badgeClass =
                  PUBLICATION_BADGE_CLASS[item.publication_status] ??
                  "bg-slate-100 text-slate-800 border-slate-200";
                return (
                  <article
                    key={item.id}
                    data-testid={`qb-card-${item.stable_code}`}
                    onClick={() => void handleSelectQuestion(item)}
                    className={`cursor-pointer rounded-2xl border bg-white p-4 shadow-xs transition ${
                      selected
                        ? "border-indigo-600 ring-2 ring-indigo-100"
                        : "border-slate-200 hover:border-indigo-300"
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-xs font-bold text-indigo-800">
                          {item.stable_code}
                        </span>
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${badgeClass}`}>
                          {item.publication_status}
                        </span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-700">
                          {ITEM_TYPE_LABELS[item.item_type] ?? item.item_type}
                        </span>
                        <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-800">
                          {item.difficulty_level}
                        </span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
                          v{item.version_no}
                        </span>
                      </div>
                      <span className="text-[11px] font-medium text-slate-500">
                        {item.visibility_scope} · {item.rights_status}
                      </span>
                    </div>

                    {taxNode && (
                      <p className="mt-1.5 text-[11px] font-semibold text-indigo-700">
                        Chủ đề: {taxNode.code} — {taxNode.name}
                      </p>
                    )}

                    <div className="mt-2 text-sm text-slate-900">
                      <SafeKatexText text={item.stem_text || "(Chưa có nội dung dẫn)"} />
                    </div>

                    {item.options.length > 0 && (
                      <ul className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                        {item.options.map((opt, idx) => (
                          <li
                            key={`${item.id}-opt-${idx}`}
                            className="rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700"
                          >
                            <span className="mr-1 font-bold text-slate-900">
                              {opt.key ?? String.fromCharCode(65 + idx)}.
                            </span>
                            <SafeKatexText text={opt.text ?? ""} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </article>
                );
              })
            )}

            {hasMore && nextCursor && (
              <div className="pt-1">
                <button
                  type="button"
                  disabled={loadingMore}
                  onClick={() => void loadQuestions(nextCursor, true)}
                  data-testid="qb-load-more-btn"
                  className="w-full rounded-xl border border-indigo-200 bg-white px-4 py-2.5 text-xs font-bold text-indigo-800 hover:bg-indigo-50 disabled:opacity-50"
                >
                  {loadingMore ? "Đang tải thêm..." : "Tải thêm câu hỏi (Cursor)"}
                </button>
              </div>
            )}
          </section>

          <aside className="lg:col-span-5" aria-label="Chi tiết câu hỏi & Phiên bản">
            <div className="sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
              {!selectedQuestionId ? (
                <div className="text-xs text-slate-600">
                  <p className="font-bold text-slate-800">Chi tiết & Lịch sử Phiên bản</p>
                  <p className="mt-1">
                    Chọn một câu hỏi ở danh sách bên trái để kiểm tra chi tiết phiên bản và rẽ nhánh bản nháp mới.
                  </p>
                </div>
              ) : loadingDetail ? (
                <p className="text-xs text-slate-600">Đang tải chi tiết câu hỏi...</p>
              ) : detail ? (
                <div data-testid="qb-detail-panel" className="space-y-4">
                  <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 pb-3">
                    <div>
                      <p className="font-mono text-sm font-extrabold text-indigo-900">
                        {detail.question.stable_code}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-500">
                        ID: <code>{detail.question.id}</code>
                      </p>
                    </div>
                    <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-bold text-slate-700">
                      {detail.question.publication_status} · {detail.question.verification_status}
                    </span>
                  </div>

                  {detail.shown_version && (
                    <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                      <div className="flex items-center justify-between text-[11px] font-bold text-slate-700">
                        <span>Phiên bản đang xem: v{detail.shown_version.version_no}</span>
                        <span>
                          {detail.shown_version.is_immutable ? "Bất biến (Immutable)" : "Có thể chỉnh sửa"}
                        </span>
                      </div>
                      <div className="text-xs text-slate-900">
                        <SafeKatexText text={detail.shown_version.stem?.text ?? ""} />
                      </div>
                    </div>
                  )}

                  <div>
                    <h2 className="text-xs font-extrabold uppercase tracking-wide text-slate-700">
                      Lịch sử phiên bản ({detail.versions.length})
                    </h2>
                    <ul className="mt-2 space-y-1.5">
                      {detail.versions.map((ver) => (
                        <li
                          key={ver.id}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-1.5 text-[11px]"
                        >
                          <div>
                            <span className="font-bold text-slate-900">v{ver.version_no}</span>
                            {ver.is_current && (
                              <span className="ml-1.5 rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-bold text-indigo-800">
                                Hiện hành
                              </span>
                            )}
                            <span className="ml-2 text-slate-600">{ver.change_reason}</span>
                          </div>
                          <span className="text-[10px] text-slate-500">
                            {ver.is_immutable ? "Khoá" : "Nháp"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <form onSubmit={handleCreateVersionFork} className="space-y-2.5 border-t border-slate-100 pt-3">
                    <h3 className="text-xs font-extrabold text-slate-900">
                      Tạo phiên bản nháp mới (Fork Version)
                    </h3>
                    <div>
                      <label className="mb-1 block text-[11px] font-semibold text-slate-700">
                        Lý do cập nhật
                      </label>
                      <input
                        type="text"
                        value={forkReason}
                        onChange={(e) => setForkReason(e.target.value)}
                        data-testid="qb-fork-reason-input"
                        className="w-full rounded-xl border border-slate-300 px-3 py-1.5 text-xs"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] font-semibold text-slate-700">
                        Nội dung câu mới (hỗ trợ LaTeX $...$)
                      </label>
                      <textarea
                        rows={3}
                        value={forkStemText}
                        onChange={(e) => setForkStemText(e.target.value)}
                        data-testid="qb-fork-stem-input"
                        className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs"
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={forking || !hasToken}
                      data-testid="qb-fork-submit-btn"
                      className="rounded-xl bg-indigo-700 px-3.5 py-2 text-xs font-bold text-white hover:bg-indigo-800 disabled:opacity-50"
                    >
                      {forking ? "Đang tạo phiên bản..." : "Tạo phiên bản nháp mới"}
                    </button>
                  </form>
                </div>
              ) : null}
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}
