"use client";

import React, { useEffect, useMemo, useState } from "react";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { WorkspaceApiAlert } from "@/components/workspace/WorkspaceApiAlert";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import type { BoundingBox } from "@/lib/bbox-reviewer";
import { resolveWorkspaceUiState, type UserTier, type WorkspaceUiInput } from "@/lib/ui-state-machine";
import { isUuid, shouldHydrateLive, shouldSyncLiveEntity, workspaceCall } from "@/lib/workspace-api";
import { PdfBboxCanvas } from "./PdfBboxCanvas";
import { QuestionInspectorPanel } from "./QuestionInspectorPanel";
import {
  createDemoImportReviewSession,
  type BboxRegionKind,
  type ImportReviewSessionData,
  type ReviewDocumentPage,
  type ReviewQuestionItem,
} from "./types";

interface RemoteQuestionPayload {
  readonly question_no?: number;
  readonly points?: number;
  readonly page_number?: number;
  readonly item_type?: string;
  readonly stem_md?: string | null;
  readonly options_md?: string[];
  readonly paired_answer?: string | null;
  readonly bbox_json?: BoundingBox | null;
  readonly provenance_id?: string | null;
  readonly original_question_label?: string | null;
  readonly question_id?: string | null;
}

interface RemoteExamImportResponse {
  readonly id?: string;
  readonly document_version_id?: string;
  readonly rights_status?: string;
  readonly status?: string;
  readonly created_question_ids?: string[];
  readonly provenance_records?: Array<{
    readonly id?: string;
    readonly bbox_json?: BoundingBox | null;
  }>;
  readonly proposed_structure_json?: {
    readonly document_title?: string | null;
    readonly expected_total_points?: number | null;
    readonly pages?: Array<{
      readonly page_no?: number;
      readonly width_pt?: number;
      readonly height_pt?: number;
      readonly heading?: string;
    }>;
    readonly sections?: Array<{
      readonly section_code?: string;
      readonly title?: string;
      readonly questions?: RemoteQuestionPayload[];
    }>;
  };
}

export function buildUpdateExamImportPayload(items: readonly ReviewQuestionItem[]) {
  const sectionBuckets = new Map<
    string,
    { section_code: string; title: string; questions: Record<string, unknown>[] }
  >();
  let totalPoints = 0;

  items.forEach((item, idx) => {
    const code = item.sectionCode || "PART_I_MCQ";
    const title = item.sectionTitle || "Phần câu hỏi";
    let bucket = sectionBuckets.get(code);
    if (!bucket) {
      bucket = { section_code: code, title, questions: [] };
      sectionBuckets.set(code, bucket);
    }
    const pts = typeof item.points === "number" && item.points > 0 ? item.points : 1.0;
    totalPoints += pts;
    const questionRegion =
      item.regions.find((r) => r.kind === "QUESTION") ?? item.regions[0] ?? null;

    bucket.questions.push({
      question_no: item.orderIndex || idx + 1,
      points: pts,
      page_number: item.pageNo || 1,
      paired_answer: item.sourceAnswer.trim() || null,
      item_type: item.questionType,
      stem_md: item.stemMarkdown,
      options_md: item.options.map((opt) => opt.label),
      bbox_json: questionRegion ? questionRegion.box : null,
      original_question_label: item.stableCode,
    });
  });

  return {
    sections: Array.from(sectionBuckets.values()),
    expected_total_points: Number(totalPoints.toFixed(2)) || 10.0,
  };
}

function hydrateSessionFromRemote(
  runId: string,
  remote: RemoteExamImportResponse,
  previous: ImportReviewSessionData,
): {
  session: ImportReviewSessionData;
  promotedCount: number;
} {
  const provenanceMap = new Map<string, BoundingBox>();
  for (const prov of remote.provenance_records ?? []) {
    if (prov.id && prov.bbox_json) {
      provenanceMap.set(prov.id, prov.bbox_json);
    }
  }

  const rawSections = remote.proposed_structure_json?.sections ?? [];
  const mappedItems: ReviewQuestionItem[] = [];
  rawSections.forEach((sec, secIdx) => {
    const secCode = sec.section_code || `PART_${secIdx + 1}`;
    const secTitle = sec.title || `Phần ${secIdx + 1}`;
    for (const q of sec.questions ?? []) {
      const idx = mappedItems.length;
      const rawType = String(q.item_type ?? "SINGLE_CHOICE").toUpperCase();
      const questionType: ReviewQuestionItem["questionType"] =
        rawType === "CLUSTER_TRUE_FALSE" || rawType === "SHORT_NUMERIC" || rawType === "ESSAY"
          ? rawType
          : "SINGLE_CHOICE";
      const optionLetters =
        questionType === "CLUSTER_TRUE_FALSE" ? ["a", "b", "c", "d"] : ["A", "B", "C", "D"];
      const options = (q.options_md ?? []).slice(0, 4).map((label, optIdx) => ({
        key: optionLetters[optIdx] ?? String(optIdx + 1),
        label,
      }));
      const box = q.bbox_json ?? (q.provenance_id ? provenanceMap.get(q.provenance_id) ?? null : null);
      const itemStatus: ReviewQuestionItem["verificationStatus"] = q.question_id
        ? "VERIFIED"
        : "AUTO_VERIFIED";
      mappedItems.push({
        id: `import-q-${q.question_no ?? idx + 1}-${idx}`,
        orderIndex: q.question_no ?? idx + 1,
        pageNo: q.page_number ?? 1,
        stableCode: q.original_question_label || `Câu ${q.question_no ?? idx + 1}`,
        questionType,
        stemMarkdown: q.stem_md || `Câu ${q.question_no ?? idx + 1}`,
        options,
        sourceAnswer: q.paired_answer ?? "",
        sympyAnswer: q.paired_answer ?? "",
        sympyTraceSummary: q.question_id
          ? `Đã lưu vào Ngân hàng câu hỏi (${q.question_id})`
          : "Đã bóc tách từ tệp nguồn PDF — kiểm tra lại đáp án & BBox trước khi duyệt.",
        verificationStatus: itemStatus,
        rightsStatus:
          (remote.rights_status as ReviewQuestionItem["rightsStatus"]) || "PENDING_REVIEW",
        regions: box
          ? [
              {
                id: `reg-${idx + 1}`,
                kind: "QUESTION",
                label: q.original_question_label || `Câu ${q.question_no ?? idx + 1}`,
                box,
              },
            ]
          : [],
        sectionCode: secCode,
        sectionTitle: secTitle,
        points: typeof q.points === "number" ? q.points : 1.0,
        backendQuestionId: q.question_id ?? null,
      });
    }
  });

  const remotePages = remote.proposed_structure_json?.pages ?? [];
  const pageNosFromItems = Array.from(new Set(mappedItems.map((it) => it.pageNo))).sort((a, b) => a - b);
  const effectivePages: ReviewDocumentPage[] =
    remotePages.length > 0
      ? remotePages.map((p, idx) => {
          const pNo = p.page_no ?? idx + 1;
          return {
            pageNo: pNo,
            width: Number(p.width_pt) > 0 ? Number(p.width_pt) : 595,
            height: Number(p.height_pt) > 0 ? Number(p.height_pt) : 842,
            heading: p.heading || `Trang ${pNo} / ${remotePages.length}`,
            previewLines: [],
            previewImageUrl: `/api/v1/exam-imports/${encodeURIComponent(runId)}/pages/${pNo}.png?dpi=150`,
          };
        })
      : (pageNosFromItems.length > 0 ? pageNosFromItems : [1]).map((pNo) => ({
          pageNo: pNo,
          width: 595,
          height: 842,
          heading: `Trang ${pNo}`,
          previewLines: [],
          previewImageUrl: `/api/v1/exam-imports/${encodeURIComponent(runId)}/pages/${pNo}.png?dpi=150`,
        }));

  const docTitle =
    remote.proposed_structure_json?.document_title?.trim() ||
    `Phiên duyệt đề (${runId.slice(0, 8)})`;
  const sourceName = remote.document_version_id
    ? `document-v-${remote.document_version_id.slice(0, 8)}.pdf`
    : `${runId.slice(0, 8)}.pdf`;

  return {
    session: {
      ...previous,
      runId,
      documentTitle: docTitle,
      sourceName,
      pages: effectivePages,
      items: mappedItems,
    },
    promotedCount: remote.created_question_ids?.length ?? 0,
  };
}

export interface BboxReviewerShellProps {
  runId: string;
  initialSession?: ImportReviewSessionData;
  initialUserTier?: UserTier;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  fetchFn?: typeof fetch;
  apiBase?: string;
}

export function BboxReviewerShell({
  runId,
  initialSession,
  initialUserTier = "TEACHER_PRO",
  initialUiOverrides,
  fetchFn,
  apiBase = "",
}: BboxReviewerShellProps) {
  const [userTier, setUserTier] = useState<UserTier>(initialUserTier);
  const [session, setSession] = useState<ImportReviewSessionData>(
    () => initialSession ?? createDemoImportReviewSession(runId),
  );
  const [selectedItemId, setSelectedItemId] = useState<string>(
    () => (initialSession ?? createDemoImportReviewSession(runId)).items[0]?.id ?? "",
  );
  const [activePageNo, setActivePageNo] = useState<number>(
    () => (initialSession ?? createDemoImportReviewSession(runId)).items[0]?.pageNo ?? 1,
  );
  const [activeRegionKind, setActiveRegionKind] = useState<BboxRegionKind>("QUESTION");
  const [zoomPercent, setZoomPercent] = useState<number>(100);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conflictDismissed, setConflictDismissed] = useState(false);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [promotedQuestionCount, setPromotedQuestionCount] = useState(0);

  useEffect(() => {
    if (!shouldHydrateLive({ hasInitialData: Boolean(initialSession), apiBase, entityIds: [runId] })) return;
    let active = true;
    void workspaceCall<RemoteExamImportResponse>({
      url: `/api/v1/exam-imports/${encodeURIComponent(runId)}/structure`,
      method: "GET",
      fetchFn,
      apiBase,
      redirectOn401: false,
    }).then((result) => {
      if (!active) return;
      if (!result.ok) {
        if (result.status !== 404) {
          setApiError(result.error);
          setApiErrorStatus(result.status);
        }
        return;
      }
      const remote = result.data;
      if (!remote?.proposed_structure_json) return;
      const hydrated = hydrateSessionFromRemote(
        runId,
        remote,
        createDemoImportReviewSession(runId),
      );
      setSession(hydrated.session);
      setSelectedItemId(hydrated.session.items[0]?.id ?? "");
      setActivePageNo(hydrated.session.pages[0]?.pageNo ?? 1);
      setPromotedQuestionCount(hydrated.promotedCount);
      setHasUnsavedChanges(false);
    });
    return () => {
      active = false;
    };
  }, [apiBase, fetchFn, initialSession, runId]);

  const selectedItem = useMemo(
    () => session.items.find((item) => item.id === selectedItemId) ?? session.items[0] ?? null,
    [session.items, selectedItemId],
  );

  const activePage = useMemo(
    () => session.pages.find((p) => p.pageNo === activePageNo) ?? session.pages[0] ?? null,
    [session.pages, activePageNo],
  );

  const pageItems = useMemo(
    () => session.items.filter((item) => item.pageNo === (activePage?.pageNo ?? 1)),
    [session.items, activePage],
  );

  const uiResolution = useMemo(
    () =>
      resolveWorkspaceUiState({
        isLoading: false,
        itemCount: session.items.length,
        requiresProAsset: session.requiresProAsset,
        userTier,
        previewPdfUrl: session.previewPdfUrl,
        rawAssetUrl: session.rawAssetUrl,
        ...initialUiOverrides,
        ...(conflictDismissed ? { isSequenceConflict409: false } : {}),
      }),
    [session, userTier, initialUiOverrides, conflictDismissed],
  );

  const handleSelectItem = (itemId: string) => {
    setSelectedItemId(itemId);
    const target = session.items.find((item) => item.id === itemId);
    if (target) {
      setActivePageNo(target.pageNo);
      setZoomPercent(120);
      if (target.regions.length > 0 && !target.regions.some((r) => r.kind === activeRegionKind)) {
        setActiveRegionKind(target.regions[0]!.kind);
      }
      setActionFeedback(`Đã phóng to trang ${target.pageNo} tại vùng ${target.stableCode}.`);
    }
  };

  const handleUpdateItem = (itemId: string, patch: Partial<ReviewQuestionItem>) => {
    setHasUnsavedChanges(true);
    setSession((prev) => ({
      ...prev,
      items: prev.items.map((it) => (it.id === itemId ? { ...it, ...patch } : it)),
    }));
  };

  const handleRecropRegion = (itemId: string, kind: BboxRegionKind, nextBox: BoundingBox) => {
    setHasUnsavedChanges(true);
    setSession((prev) => ({
      ...prev,
      items: prev.items.map((item) => {
        if (item.id !== itemId) return item;
        const hasKind = item.regions.some((r) => r.kind === kind);
        const regions = hasKind
          ? item.regions.map((r) => (r.kind === kind ? { ...r, box: nextBox } : r))
          : [
              ...item.regions,
              {
                id: `${item.id}-${kind.toLowerCase()}`,
                kind,
                label: `${item.stableCode} · ${kind}`,
                box: nextBox,
              },
            ];
        return { ...item, regions };
      }),
    }));
    setActionFeedback(
      `Đã cắt lại ${kind} (${nextBox.x0}, ${nextBox.y0}, ${nextBox.x1}, ${nextBox.y1}).`,
    );
  };

  const handleMarkVerified = (itemId: string) => {
    handleUpdateItem(itemId, { verificationStatus: "VERIFIED" });
    setActionFeedback("Đã xác nhận chuyên môn VERIFIED.");
  };

  const postStudioAction = async (
    path: string,
    body: unknown,
    fallbackMessage: string,
    entityIds: readonly string[],
  ) => {
    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    try {
      if (!shouldSyncLiveEntity({ fetchFn, apiBase, entityIds })) {
        setActionFeedback(`${fallbackMessage} (mô phỏng demo cục bộ)`);
        return;
      }
      const result = await workspaceCall({
        url: path,
        method: path.includes("/structure") ? "PATCH" : "POST",
        json: body,
        fetchFn,
        apiBase,
      });
      if (!result.ok) {
        setApiError(result.error);
        setApiErrorStatus(result.status);
        setActionFeedback(null);
        return false;
      }
      setActionFeedback(fallbackMessage);
      return true;
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAutoExtract = async () => {
    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    try {
      const res = await workspaceCall<RemoteExamImportResponse>({
        url: `/api/v1/exam-imports/${encodeURIComponent(runId)}/auto-extract`,
        method: "POST",
        json: {},
        fetchFn,
        apiBase,
      });
      if (!res.ok) {
        setApiError(res.error);
        setApiErrorStatus(res.status);
        return;
      }
      if (res.data) {
        const hydrated = hydrateSessionFromRemote(runId, res.data, session);
        setSession(hydrated.session);
        setSelectedItemId(hydrated.session.items[0]?.id ?? "");
        setActivePageNo(hydrated.session.pages[0]?.pageNo ?? 1);
        setActionFeedback(
          `Đã tự động bóc tách ${hydrated.session.items.length} câu hỏi & BBox từ file PDF.`,
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSaveAndApprove = async () => {
    setIsSubmitting(true);
    setApiError(null);
    setApiErrorStatus(null);
    try {
      if (!shouldSyncLiveEntity({ fetchFn, apiBase, entityIds: [runId] })) {
        setActionFeedback(`Đã duyệt Approve cho câu ${selectedItem?.stableCode ?? ""} (mô phỏng demo cục bộ)`);
        return;
      }
      if (hasUnsavedChanges) {
        const saveRes = await workspaceCall({
          url: `/api/v1/exam-imports/${encodeURIComponent(runId)}/structure`,
          method: "PATCH",
          json: buildUpdateExamImportPayload(session.items),
          fetchFn,
          apiBase,
        });
        if (!saveRes.ok) {
          setApiError(saveRes.error);
          setApiErrorStatus(saveRes.status);
          return;
        }
        setHasUnsavedChanges(false);
      }

      const approveRes = await workspaceCall<RemoteExamImportResponse>({
        url: `/api/v1/exam-imports/${encodeURIComponent(runId)}/approve`,
        method: "POST",
        json: {
          require_answer_keys: true,
          generate_branded_pdf: false,
          promote_to_question_bank: true,
        },
        fetchFn,
        apiBase,
      });
      if (!approveRes.ok) {
        setApiError(approveRes.error);
        setApiErrorStatus(approveRes.status);
        return;
      }
      const createdIds = approveRes.data?.created_question_ids ?? [];
      setPromotedQuestionCount(createdIds.length);
      setActionFeedback(
        createdIds.length > 0
          ? `Đã duyệt phiên và lưu ${createdIds.length} câu hỏi vào Ngân hàng câu hỏi!`
          : `Đã duyệt phiên ${runId.slice(0, 8)} thành công.`,
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ErrorBoundary
      errorCode={
        uiResolution.kind === "ErrorBoundary"
          ? (initialUiOverrides?.fatalErrorCode ?? "STUDIO_REVIEW_ERROR")
          : null
      }
    >
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <StudioHeader activeRoute="ws3-review" userTier={userTier} onTierChange={setUserTier} />

        {uiResolution.kind === "SequenceConflictModal" && (
          <SequenceConflictModal
            conflict={{
              questionVersionId: runId,
              questionLabel: session.documentTitle,
              localPayload: { selectedItemId },
              serverPayload: { status: "UPDATED_BY_PEER" },
            }}
            onResolve={() => setConflictDismissed(true)}
          />
        )}

        {uiResolution.kind === "OfflineRetryBanner" && (
          <OfflineRetryBanner
            pendingCount={initialUiOverrides?.pendingAutosaveCount ?? 0}
            message={uiResolution.bannerMessage}
          />
        )}

        {uiResolution.kind === "LoadingSkeleton" ? (
          <LoadingSkeleton title="Đang tải dữ liệu Bounding-Box Reviewer..." />
        ) : uiResolution.kind === "ErrorBoundary" ? (
          <ErrorBoundary
            errorCode={initialUiOverrides?.fatalErrorCode ?? "STUDIO_REVIEW_ERROR"}
            errorMessage={uiResolution.bannerMessage}
          />
        ) : uiResolution.kind === "LockedProPreview" ? (
          <main data-testid="ws3-locked-pro-container" className="mx-auto max-w-4xl px-4 py-8">
            <LockedProPreview
              title={`Tài liệu bản quyền giới hạn: ${session.documentTitle}`}
              userTier={userTier}
              requiresProAsset={session.requiresProAsset}
              previewPdfUrl={uiResolution.exposedAssetUrl}
              rawAssetUrl={null}
              onUpgradeClick={() => setUserTier("TEACHER_PRO")}
            />
          </main>
        ) : uiResolution.kind === "EmptyState" || !selectedItem || !activePage ? (
          <div className="mx-auto max-w-3xl space-y-4 px-4 py-8">
            {apiError && (
              <WorkspaceApiAlert
                message={apiError}
                status={apiErrorStatus}
                onDismiss={() => setApiError(null)}
              />
            )}
            <EmptyState
              title="Chưa có câu hỏi được bóc tách trong phiên nhập"
              description={`Phiên ${runId} chưa có vùng BBox hoặc câu hỏi ứng viên để kiểm duyệt.`}
            />
            {isUuid(runId) && (
              <div className="flex justify-center">
                <button
                  type="button"
                  data-testid="ws3-auto-extract-btn"
                  disabled={isSubmitting}
                  onClick={() => void handleAutoExtract()}
                  className="rounded-xl bg-indigo-700 px-4 py-2 text-xs font-bold text-white hover:bg-indigo-800 disabled:opacity-50"
                >
                  {isSubmitting ? "Đang bóc tách PDF..." : "Tự động bóc tách câu hỏi từ PDF"}
                </button>
              </div>
            )}
          </div>
        ) : (
          <main data-testid="ws3-split-layout" className="mx-auto max-w-[1600px] px-4 py-5">
            {apiError && (
              <div className="mb-4">
                <WorkspaceApiAlert
                  message={apiError}
                  status={apiErrorStatus}
                  onDismiss={() => setApiError(null)}
                />
              </div>
            )}
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-xs">
              <div>
                <h1 className="text-lg font-extrabold text-slate-900">{session.documentTitle}</h1>
                <p className="text-xs text-slate-500">
                  Run ID: <code>{session.runId}</code> · Tệp nguồn: <code>{session.sourceName}</code>
                </p>
              </div>
              <span className="rounded-full bg-indigo-50 px-3 py-1 text-xs font-bold text-indigo-800">
                Side-by-Side 50% / 50% BBox Reviewer
              </span>
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <PdfBboxCanvas
                page={activePage}
                allPages={session.pages}
                pageItems={pageItems}
                selectedItem={selectedItem}
                activeRegionKind={activeRegionKind}
                onChangePage={(pageNo) => {
                  setActivePageNo(pageNo);
                  const firstOnPage = session.items.find((it) => it.pageNo === pageNo);
                  if (firstOnPage) setSelectedItemId(firstOnPage.id);
                }}
                onSelectRegionKind={setActiveRegionKind}
                onSelectItem={handleSelectItem}
                onRecropRegion={handleRecropRegion}
                zoomPercent={zoomPercent}
                onZoomChange={setZoomPercent}
                fetchFn={fetchFn}
                apiBase={apiBase}
              />

              <QuestionInspectorPanel
                items={session.items}
                selectedItem={selectedItem}
                actionFeedback={actionFeedback}
                isSubmitting={isSubmitting}
                promotedQuestionCount={promotedQuestionCount}
                onSelectItem={handleSelectItem}
                onUpdateItem={handleUpdateItem}
                onMarkVerified={handleMarkVerified}
                onSaveStructure={() =>
                  void postStudioAction(
                    `/api/v1/exam-imports/${encodeURIComponent(runId)}/structure`,
                    buildUpdateExamImportPayload(session.items),
                    `Đã lưu cấu trúc & vùng cắt BBox cho ${selectedItem.stableCode}.`,
                    [runId],
                  ).then((ok) => {
                    if (ok) setHasUnsavedChanges(false);
                  })
                }
                onApproveItem={() => void handleSaveAndApprove()}
                onPublishItem={(itemId) =>
                  void postStudioAction(
                    selectedItem.backendQuestionId
                      ? `/api/v1/questions/${encodeURIComponent(selectedItem.backendQuestionId)}/publish`
                      : `/api/v1/review-tasks/${encodeURIComponent(itemId)}/decisions`,
                    selectedItem.backendQuestionId ? {} : { decision: "PUBLISH" },
                    `Đã Publish câu ${selectedItem.stableCode} lên Kho chuẩn.`,
                    [selectedItem.backendQuestionId ?? itemId],
                  )
                }
              />
            </div>
          </main>
        )}
      </div>
    </ErrorBoundary>
  );
}
