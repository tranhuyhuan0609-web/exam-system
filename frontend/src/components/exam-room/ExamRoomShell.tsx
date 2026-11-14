"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  EmptyState,
  ErrorBoundary,
  LoadingSkeleton,
  LockedProPreview,
  OfflineRetryBanner,
  type SequenceConflictItem,
  SequenceConflictModal,
} from "@/components/workspace/WorkspaceStateViews";
import {
  createExamAutosaveStore,
  type ExamAutosaveStore,
  type ExamPayload,
  type QueueState,
} from "@/lib/exam-autosave-store";
import { type CountdownTick, ExamCountdown } from "@/lib/exam-countdown";
import { evaluateClusterTrueFalseState } from "@/lib/exam-input-policy";
import { ExamProctorTracker } from "@/lib/exam-proctor";
import {
  ExamSubmissionCoordinator,
  type SubmissionStateSnapshot,
  type SubmitAttemptRequest,
  type SubmitAttemptResponse,
} from "@/lib/exam-submission";
import { ServerClock } from "@/lib/server-clock";
import {
  resolveWorkspaceUiState,
  type WorkspaceUiInput,
} from "@/lib/ui-state-machine";
import { ExamQuestionCard } from "./ExamQuestionCard";
import { ExamQuestionPalette } from "./ExamQuestionPalette";
import { ExamSubmitModal } from "./ExamSubmitModal";
import {
  type AnswerMap,
  type ChoiceLetter,
  type ClusterSubKey,
  createDemoExamAttempt,
  createEmptyAnswerState,
  type ExamAttemptData,
  type ExamQuestion,
  isPart1Answered,
  isPart3Answered,
  type QuestionAnswerState,
} from "./types";

export interface ExamRoomShellProps {
  attemptId: string;
  initialAttempt?: ExamAttemptData | null;
  initialUiOverrides?: Partial<WorkspaceUiInput>;
  initialConflict?: SequenceConflictItem | null;
  autosaveStore?: ExamAutosaveStore;
  proctorTracker?: ExamProctorTracker;
  clock?: ServerClock;
  fetchFn?: typeof fetch;
  apiBase?: string;
  isDemoMode?: boolean;
  onUpgradeClick?: () => void;
}

function formatCountdownHms(remainingSeconds: number): string {
  const safe = Math.max(0, Math.floor(remainingSeconds));
  const hours = Math.floor(safe / 3600);
  const mins = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return [hours, mins, secs].map((n) => String(n).padStart(2, "0")).join(":");
}

function buildExamPayload(
  question: ExamQuestion,
  state: QuestionAnswerState,
): ExamPayload {
  if (question.part === "PART_1") {
    return {
      kind: "PART_1",
      choice: state.choice,
      eliminated: [...state.eliminated],
    };
  }
  if (question.part === "PART_2") {
    return {
      kind: "PART_2",
      cluster: {
        a: state.cluster.a ?? null,
        b: state.cluster.b ?? null,
        c: state.cluster.c ?? null,
        d: state.cluster.d ?? null,
      },
    };
  }
  return {
    kind: "PART_3",
    short_text: state.shortText,
  };
}

function parseServerPayloadToAnswer(
  question: ExamQuestion,
  prev: QuestionAnswerState,
  serverPayload: unknown,
): QuestionAnswerState {
  if (!serverPayload || typeof serverPayload !== "object") {
    return prev;
  }
  const obj = serverPayload as Record<string, unknown>;
  if (question.part === "PART_1") {
    const choice =
      obj.choice === "A" || obj.choice === "B" || obj.choice === "C" || obj.choice === "D"
        ? obj.choice
        : null;
    const eliminated = Array.isArray(obj.eliminated)
      ? obj.eliminated.filter(
          (item): item is ChoiceLetter =>
            item === "A" || item === "B" || item === "C" || item === "D",
        )
      : prev.eliminated;
    return { ...prev, choice, eliminated };
  }
  if (question.part === "PART_2") {
    const rawCluster =
      obj.cluster && typeof obj.cluster === "object"
        ? (obj.cluster as Record<string, unknown>)
        : obj;
    return {
      ...prev,
      cluster: {
        a: typeof rawCluster.a === "boolean" ? rawCluster.a : null,
        b: typeof rawCluster.b === "boolean" ? rawCluster.b : null,
        c: typeof rawCluster.c === "boolean" ? rawCluster.c : null,
        d: typeof rawCluster.d === "boolean" ? rawCluster.d : null,
      },
    };
  }
  const shortVal =
    typeof obj.short_text === "string"
      ? obj.short_text
      : typeof obj.shortText === "string"
        ? obj.shortText
        : "";
  return { ...prev, shortText: shortVal };
}

export function ExamRoomShell({
  attemptId,
  initialAttempt,
  initialUiOverrides,
  initialConflict = null,
  autosaveStore: injectedStore,
  proctorTracker: injectedProctor,
  clock: injectedClock,
  fetchFn,
  apiBase = "",
  isDemoMode = false,
  onUpgradeClick,
}: ExamRoomShellProps) {
  const [attempt, setAttempt] = useState<ExamAttemptData | null>(() => {
    if (initialAttempt !== undefined) return initialAttempt;
    if (isDemoMode) return createDemoExamAttempt(attemptId);
    return null;
  });
  const [loading, setLoading] = useState<boolean>(
    initialAttempt === undefined && !isDemoMode,
  );
  const [fatalErrorCode, setFatalErrorCode] = useState<string | null>(null);
  const [fatalErrorMsg, setFatalErrorMsg] = useState<string | null>(null);

  const [answers, setAnswers] = useState<AnswerMap>(
    () => initialAttempt?.initialAnswers ?? {},
  );
  const [activeIndex, setActiveIndex] = useState<number>(0);

  const [isOffline, setIsOffline] = useState<boolean>(() =>
    typeof navigator !== "undefined" ? !navigator.onLine : false,
  );
  const [isRetryingSync, setIsRetryingSync] = useState<boolean>(false);
  const [queueState, setQueueState] = useState<QueueState>({
    pending: 0,
    conflict: 0,
    rejected: 0,
    authRequired: false,
  });

  const [activeConflict, setActiveConflict] = useState<SequenceConflictItem | null>(
    initialConflict,
  );
  const [isResolvingConflict, setIsResolvingConflict] = useState<boolean>(false);
  const [countdownTick, setCountdownTick] = useState<CountdownTick | null>(null);
  const [timeWarningBanner, setTimeWarningBanner] = useState<string | null>(null);
  const [submitModalOpen, setSubmitModalOpen] = useState<boolean>(false);

  const [submissionState, setSubmissionState] = useState<SubmissionStateSnapshot>({
    attemptId,
    phase: "idle",
    inputLocked: false,
    trigger: null,
    submitIdempotencyKey: null,
    retryCount: 0,
    nextRetryDelayMs: null,
    pendingCount: 0,
    receiptId: null,
    submittedAtServerIso: null,
    errorCode: null,
    errorMessage: null,
    unsyncedAnswerIds: [],
    canSubmitWithUnsynced: false,
  });

  const proctorRef = useRef<ExamProctorTracker>(injectedProctor ?? new ExamProctorTracker());
  const clockRef = useRef<ServerClock>(
    injectedClock ?? new ServerClock({ fetchFn }),
  );
  const storeRef = useRef<ExamAutosaveStore | null>(injectedStore ?? null);
  const answersRef = useRef<AnswerMap>(answers);
  answersRef.current = answers;
  const attemptRef = useRef<ExamAttemptData | null>(attempt);
  attemptRef.current = attempt;

  const conflictServerPayloadRef = useRef<Map<string, unknown>>(new Map());

  const ensureStore = useCallback((): ExamAutosaveStore | null => {
    if (storeRef.current) return storeRef.current;
    if (typeof indexedDB === "undefined") return null;
    storeRef.current = createExamAutosaveStore({
      apiBase,
      fetchFn,
      autoAttachWindowListeners: true,
      onConflictModal: (questionVersionId, serverPayload) => {
        conflictServerPayloadRef.current.set(questionVersionId, serverPayload);
      },
    });
    return storeRef.current;
  }, [apiBase, fetchFn]);

  const refreshQueueAndConflicts = useCallback(async () => {
    const store = ensureStore();
    if (!store) return;
    try {
      const q = await store.getQueueState(attemptId);
      setQueueState(q);
      if (q.conflict > 0) {
        const rows = await store.getPendingRows(attemptId);
        const conflictRow = rows.find((row) => row.status === "conflict");
        if (conflictRow) {
          const matchedQuestion = attemptRef.current?.questions.find(
            (item) => item.questionVersionId === conflictRow.questionVersionId,
          );
          setActiveConflict({
            rowId: conflictRow.id,
            questionVersionId: conflictRow.questionVersionId,
            questionLabel: matchedQuestion ? `Câu ${matchedQuestion.number}` : undefined,
            localPayload: conflictRow.responsePayload,
            serverPayload:
              conflictServerPayloadRef.current.get(conflictRow.questionVersionId) ?? null,
          });
        }
      }
    } catch {
      // Non-fatal in headless environments without IndexedDB
    }
  }, [attemptId, ensureStore]);

  const coordinator = useMemo(() => {
    const activeFetch = fetchFn ?? globalThis.fetch.bind(globalThis);
    return new ExamSubmissionCoordinator({
      attemptId,
      flushPendingAutosaves: async () => {
        const store = ensureStore();
        if (!store) {
          return { flushedCount: 0, remainingPendingCount: 0, status: "ok" };
        }
        await store.flush();
        await refreshQueueAndConflicts();
        const q = await store.getQueueState(attemptId);
        if (q.authRequired) {
          return {
            flushedCount: 0,
            remainingPendingCount: q.pending,
            status: "auth_required",
            errorMessage: "Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.",
          };
        }
        if (q.rejected > 0) {
          const rows = await store.getPendingRows(attemptId);
          const rejectedIds = rows
            .filter((row) => row.status === "rejected")
            .map((row) => row.questionVersionId);
          return {
            flushedCount: 0,
            remainingPendingCount: q.rejected,
            status: "rejected",
            unsyncedAnswerIds: rejectedIds,
            errorMessage: "Có câu trả lời bị từ chối khi đồng bộ lên máy chủ.",
          };
        }
        if (q.pending > 0 || q.conflict > 0) {
          return {
            flushedCount: 0,
            remainingPendingCount: q.pending + q.conflict,
            status: "offline",
            errorMessage: "Chưa đồng bộ xong hàng đợi câu trả lời ngoại tuyến.",
          };
        }
        return { flushedCount: 1, remainingPendingCount: 0, status: "ok" };
      },
      submitAttempt: async (req: SubmitAttemptRequest): Promise<SubmitAttemptResponse> => {
        try {
          const res = await activeFetch(
            `${apiBase}/api/v1/attempts/${attemptId}/submit`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "Idempotency-Key": req.submitIdempotencyKey,
              },
              body: JSON.stringify({
                submit_idempotency_key: req.submitIdempotencyKey,
                trigger: req.trigger,
                forced_with_unsynced_answers: Boolean(req.forcedWithUnsyncedAnswers),
                unsynced_answer_ids: req.unsyncedAnswerIds ?? [],
                proctor_summary: proctorRef.current.getSummary(
                  typeof performance !== "undefined" ? performance.now() : 0,
                ),
              }),
            },
          );
          if (res.status === 409) {
            return {
              status: "sequence_conflict",
              errorCode: "STALE_CLIENT_SEQUENCE",
              errorMessage: "Phát hiện xung đột đồng bộ khi nộp bài.",
            };
          }
          if (res.status === 400 || res.status === 422 || res.status === 403) {
            return {
              status: "rejected",
              errorCode: `HTTP_${res.status}`,
              errorMessage: "Máy chủ từ chối yêu cầu nộp bài.",
            };
          }
          if (!res.ok) {
            return {
              status: "retryable_error",
              errorCode: `HTTP_${res.status}`,
              errorMessage: "Không thể kết nối máy chủ khi nộp bài. Vui lòng thử lại.",
            };
          }
          const body = (await res.json()) as {
            status?: "submitted" | "already_submitted";
            receipt_id?: string;
            submitted_at_server_iso?: string;
          };
          return {
            status: body.status ?? "submitted",
            receiptId: body.receipt_id ?? `RCPT-${attemptId.slice(0, 8)}`,
            submittedAtServerIso: body.submitted_at_server_iso ?? new Date().toISOString(),
          };
        } catch {
          return {
            status: "retryable_error",
            errorCode: "NETWORK_ERROR",
            errorMessage: "Mất kết nối mạng khi nộp bài. Hàng đợi vẫn đang giữ nguyên đáp án.",
          };
        }
      },
      onStateChange: (snapshot) => {
        setSubmissionState(snapshot);
      },
    });
  }, [apiBase, attemptId, ensureStore, fetchFn, refreshQueueAndConflicts]);

  const loadAttempt = useCallback(async () => {
    if (initialAttempt !== undefined) {
      setAttempt(initialAttempt);
      setLoading(false);
      return;
    }
    if (isDemoMode) {
      setAttempt(createDemoExamAttempt(attemptId));
      setLoading(false);
      return;
    }

    setLoading(true);
    setFatalErrorCode(null);
    setFatalErrorMsg(null);
    const activeFetch = fetchFn ?? globalThis.fetch.bind(globalThis);

    try {
      void clockRef.current.sync(`${apiBase}/api/v1/time`).catch(() => undefined);
      const res = await activeFetch(`${apiBase}/api/v1/attempts/${attemptId}`);
      if (res.status === 403) {
        let previewUrl: string | null = null;
        try {
          const lockedBody = (await res.json()) as { preview_pdf_url?: string | null };
          previewUrl = lockedBody.preview_pdf_url ?? null;
        } catch {
          previewUrl = null;
        }
        setAttempt({
          attemptId,
          title: "Nội dung đề thi Pro",
          subjectName: "H2Edu",
          deadlineAtIso: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
          userTier: "FREE",
          requiresProAsset: true,
          previewPdfUrl: previewUrl,
          rawAssetUrl: null,
          questions: [],
        });
        setLoading(false);
        return;
      }

      if (!res.ok) {
        setFatalErrorCode(`HTTP_${res.status}`);
        setFatalErrorMsg("Không thể tải thông tin phòng thi từ máy chủ.");
        setLoading(false);
        return;
      }

      const data = (await res.json()) as ExamAttemptData;
      setAttempt(data);
      if (data.initialAnswers) {
        setAnswers(data.initialAnswers);
      }
      setLoading(false);
    } catch {
      setFatalErrorCode("NETWORK_ERROR");
      setFatalErrorMsg("Không thể kết nối tới máy chủ H2Edu.");
      setLoading(false);
    }
  }, [apiBase, attemptId, fetchFn, initialAttempt, isDemoMode]);

  useEffect(() => {
    void loadAttempt();
  }, [loadAttempt]);

  // Network online/offline listeners + auto recovery
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleOnline = () => {
      setIsOffline(false);
      const store = ensureStore();
      if (store) {
        void store.flush().then(() => refreshQueueAndConflicts());
      }
      void coordinator.onConnectionRestored();
    };
    const handleOffline = () => {
      setIsOffline(true);
    };
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [coordinator, ensureStore, refreshQueueAndConflicts]);

  // Advisory Proctoring listeners
  useEffect(() => {
    if (typeof document === "undefined") return;
    const tracker = proctorRef.current;
    const getNow = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

    const onVisibilityChange = () => {
      if (document.hidden) {
        tracker.recordVisibilityHidden(getNow());
      } else {
        tracker.recordVisibilityVisible(getNow());
      }
    };
    const onFullscreenChange = () => {
      if (!document.fullscreenElement) {
        tracker.recordFullscreenExit(getNow());
      }
    };
    const onCopy = () => {
      const len = document.getSelection()?.toString().length ?? 0;
      tracker.recordClipboardAction("COPY", getNow(), len);
    };
    const onPaste = (e: ClipboardEvent) => {
      const len = e.clipboardData?.getData("text")?.length ?? 0;
      tracker.recordClipboardAction("PASTE", getNow(), len);
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("copy", onCopy);
    document.addEventListener("paste", onPaste);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      document.removeEventListener("copy", onCopy);
      document.removeEventListener("paste", onPaste);
    };
  }, []);

  // Countdown Lifecycle
  useEffect(() => {
    if (!attempt?.deadlineAtIso) return;
    const countdown = new ExamCountdown({
      deadlineAt: attempt.deadlineAtIso,
      clock: clockRef.current,
      tickIntervalMs: 1000,
      onTick: (tick) => setCountdownTick(tick),
      onFiveMinuteWarning: () => {
        setTimeWarningBanner("Còn dưới 5 phút — vui lòng rà soát kỹ các câu đã làm!");
      },
      onOneMinuteWarning: () => {
        setTimeWarningBanner("Sắp hết giờ — còn dưới 1 phút trước khi hệ thống tự động nộp bài!");
      },
      onExpire: () => {
        setTimeWarningBanner("Đã hết thời gian làm bài — hệ thống đang tự động nộp bài!");
        void coordinator.submit("auto_expire");
      },
    });
    countdown.start();
    return () => countdown.stop();
  }, [attempt?.deadlineAtIso, coordinator]);

  const effectiveUiState = useMemo(() => {
    return resolveWorkspaceUiState({
      isLoading: initialUiOverrides?.isLoading ?? loading,
      fatalErrorCode: initialUiOverrides?.fatalErrorCode ?? fatalErrorCode,
      isSequenceConflict409:
        initialUiOverrides?.isSequenceConflict409 ??
        (activeConflict !== null || submissionState.phase === "sequence_conflict"),
      isOffline:
        initialUiOverrides?.isOffline ??
        (isOffline || submissionState.phase === "offline_retry_banner"),
      pendingAutosaveCount: initialUiOverrides?.pendingAutosaveCount ?? queueState.pending,
      itemCount:
        initialUiOverrides?.itemCount ??
        (attempt ? attempt.questions.length : 1),
      requiresProAsset:
        initialUiOverrides?.requiresProAsset ??
        attempt?.requiresProAsset ??
        false,
      userTier: initialUiOverrides?.userTier ?? attempt?.userTier ?? "STUDENT_PRO",
      previewPdfUrl:
        initialUiOverrides?.previewPdfUrl ?? attempt?.previewPdfUrl ?? null,
      rawAssetUrl: initialUiOverrides?.rawAssetUrl ?? attempt?.rawAssetUrl ?? null,
    });
  }, [
    activeConflict,
    attempt,
    fatalErrorCode,
    initialUiOverrides,
    isOffline,
    loading,
    queueState.pending,
    submissionState.phase,
  ]);

  const updateAnswerState = useCallback(
    (question: ExamQuestion, updater: (prev: QuestionAnswerState) => QuestionAnswerState) => {
      const current = answersRef.current[question.questionVersionId] ?? createEmptyAnswerState();
      const next = updater(current);
      setAnswers((prev) => ({
        ...prev,
        [question.questionVersionId]: next,
      }));

      const store = ensureStore();
      if (store) {
        const payload = buildExamPayload(question, next);
        void (async () => {
          await store.enqueue(attemptId, question.questionVersionId, payload, {
            flagged: next.flagged,
            autoFlush: false,
          });
          await refreshQueueAndConflicts();
          await store.flush();
          await refreshQueueAndConflicts();
        })().catch(() => undefined);
      }
    },
    [attemptId, ensureStore, refreshQueueAndConflicts],
  );

  const questions = attempt?.questions ?? [];
  const activeQuestion = questions[activeIndex] ?? null;
  const inputsDisabled =
    !effectiveUiState.interactiveExamAllowed ||
    submissionState.inputLocked ||
    submissionState.phase === "submitted";

  // Keyboard shortcuts (A/B/C/D for Part I, ArrowLeft/ArrowRight navigation, F flag)
  useEffect(() => {
    if (typeof window === "undefined" || !activeQuestion || inputsDisabled) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        target?.isContentEditable ||
        submitModalOpen
      ) {
        return;
      }

      const key = e.key.toUpperCase();
      if (activeQuestion.part === "PART_1" && (key === "A" || key === "B" || key === "C" || key === "D")) {
        e.preventDefault();
        const choice = key as ChoiceLetter;
        updateAnswerState(activeQuestion, (prev) => ({
          ...prev,
          choice,
          eliminated: prev.eliminated.filter((item) => item !== choice),
        }));
      } else if (key === "F") {
        e.preventDefault();
        updateAnswerState(activeQuestion, (prev) => ({
          ...prev,
          flagged: !prev.flagged,
        }));
      } else if (e.key === "ArrowLeft") {
        setActiveIndex((idx) => Math.max(0, idx - 1));
      } else if (e.key === "ArrowRight") {
        setActiveIndex((idx) => Math.min(questions.length - 1, idx + 1));
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeQuestion, inputsDisabled, questions.length, submitModalOpen, updateAnswerState]);

  const handleRetrySync = async () => {
    setIsRetryingSync(true);
    try {
      const store = ensureStore();
      if (store) {
        await store.flush();
        await refreshQueueAndConflicts();
      }
      await coordinator.onConnectionRestored();
    } finally {
      setIsRetryingSync(false);
    }
  };

  const handleResolveConflict = async (resolution: "keep_mine" | "take_server") => {
    setIsResolvingConflict(true);
    try {
      const conflict = activeConflict;
      const store = ensureStore();
      if (store && conflict?.rowId !== undefined) {
        await store.resolveConflict(conflict.rowId, resolution, {
          autoFlush: resolution === "keep_mine",
        });
      }
      if (conflict && resolution === "take_server") {
        const q = questions.find((item) => item.questionVersionId === conflict.questionVersionId);
        if (q) {
          setAnswers((prev) => {
            const curr = prev[q.questionVersionId] ?? createEmptyAnswerState();
            return {
              ...prev,
              [q.questionVersionId]: parseServerPayloadToAnswer(q, curr, conflict.serverPayload),
            };
          });
        }
      }
      setActiveConflict(null);
      await refreshQueueAndConflicts();
    } finally {
      setIsResolvingConflict(false);
    }
  };

  // Summary counters for Submit modal
  const stats = useMemo(() => {
    let answered = 0;
    let partial = 0;
    let flagged = 0;
    for (const q of questions) {
      const ans = answers[q.questionVersionId];
      if (ans?.flagged) flagged += 1;
      if (q.part === "PART_1" && isPart1Answered(ans)) answered += 1;
      else if (q.part === "PART_2") {
        const s = evaluateClusterTrueFalseState(ans?.cluster ?? {});
        if (s === "COMPLETE") answered += 1;
        else if (s === "PARTIAL_WARNING") partial += 1;
      } else if (q.part === "PART_3" && isPart3Answered(ans)) answered += 1;
    }
    return { answered, partial, flagged };
  }, [answers, questions]);

  // Top-level mandatory workspace state views
  if (effectiveUiState.kind === "LoadingSkeleton") {
    return <LoadingSkeleton />;
  }

  if (effectiveUiState.kind === "ErrorBoundary") {
    return (
      <ErrorBoundary
        errorCode={initialUiOverrides?.fatalErrorCode ?? fatalErrorCode}
        errorMessage={fatalErrorMsg ?? effectiveUiState.bannerMessage}
        onRetry={() => void loadAttempt()}
      />
    );
  }

  if (effectiveUiState.kind === "LockedProPreview") {
    return (
      <LockedProPreview
        title={attempt?.title}
        userTier={initialUiOverrides?.userTier ?? attempt?.userTier ?? "FREE"}
        requiresProAsset={initialUiOverrides?.requiresProAsset ?? attempt?.requiresProAsset ?? true}
        previewPdfUrl={initialUiOverrides?.previewPdfUrl ?? attempt?.previewPdfUrl ?? null}
        rawAssetUrl={initialUiOverrides?.rawAssetUrl ?? attempt?.rawAssetUrl ?? null}
        onUpgradeClick={onUpgradeClick}
      />
    );
  }

  if (effectiveUiState.kind === "EmptyState" || questions.length === 0) {
    return <EmptyState onAction={() => void loadAttempt()} />;
  }

  const remainingPhase = countdownTick?.phase ?? "normal";
  const timerStyle =
    remainingPhase === "one_minute_warning" || remainingPhase === "expired"
      ? "animate-pulse border-red-300 bg-red-50 text-red-800"
      : remainingPhase === "five_minute_warning"
        ? "border-amber-300 bg-amber-50 text-amber-900"
        : "border-slate-200 bg-slate-50 text-slate-900";

  return (
    <ErrorBoundary onRetry={() => void loadAttempt()}>
      <div className="min-h-screen bg-slate-50">
        {/* Sticky Exam Room Header */}
        <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur-xs">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center rounded-lg bg-blue-700 px-2.5 py-1 text-xs font-extrabold tracking-wide text-white">
                H2Edu
              </span>
              <div>
                <h1 className="text-sm font-bold text-slate-900 sm:text-base">
                  {attempt?.title ?? `Phòng thi ${attemptId}`}
                </h1>
                <p className="text-xs text-slate-500">
                  Học thông minh - Thi bứt phá • {attempt?.subjectName ?? "THPTQG 2025"}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
              {/* Autosave Status Badge */}
              <div
                data-testid="autosave-status-badge"
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700"
              >
                {queueState.conflict > 0
                  ? "Xung đột đồng bộ (409)"
                  : queueState.pending > 0
                    ? `Đang chờ lưu (${queueState.pending})`
                    : "Đã lưu đồng bộ"}
              </div>

              {/* Countdown Timer */}
              <div
                data-testid="exam-countdown-timer"
                data-phase={remainingPhase}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 font-mono text-sm font-bold tabular-nums ${timerStyle}`}
              >
                <span aria-hidden="true">⏱</span>
                <span>{formatCountdownHms(countdownTick?.remainingSeconds ?? 0)}</span>
              </div>

              <button
                type="button"
                disabled={submissionState.phase === "submitted"}
                data-testid="open-submit-modal-btn"
                onClick={() => setSubmitModalOpen(true)}
                className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-800 disabled:opacity-50"
              >
                Nộp bài
              </button>
            </div>
          </div>

          {/* Non-blocking 5m / 1m Warning */}
          {timeWarningBanner && (
            <div
              role="status"
              data-testid="countdown-warning-banner"
              className={`border-t px-4 py-2 text-center text-xs font-semibold ${
                remainingPhase === "one_minute_warning" || remainingPhase === "expired"
                  ? "border-red-200 bg-red-600 text-white"
                  : "border-amber-200 bg-amber-100 text-amber-950"
              }`}
            >
              {timeWarningBanner}
            </div>
          )}

          {/* State 5: Non-blocking Offline Retry Banner */}
          {effectiveUiState.kind === "OfflineRetryBanner" && (
            <OfflineRetryBanner
              pendingCount={queueState.pending}
              message={effectiveUiState.bannerMessage}
              onRetrySync={() => void handleRetrySync()}
              isRetrying={isRetryingSync}
            />
          )}
        </header>

        {/* Main 2-column Workspace 2 Layout */}
        <main className="mx-auto grid max-w-7xl grid-cols-1 gap-6 px-4 py-6 sm:px-6 lg:grid-cols-12">
          <div className="lg:col-span-8">
            {activeQuestion && (
              <ExamQuestionCard
                question={activeQuestion}
                totalQuestions={questions.length}
                answerState={answers[activeQuestion.questionVersionId] ?? createEmptyAnswerState()}
                disabled={inputsDisabled}
                onSelectChoice={(choice) =>
                  updateAnswerState(activeQuestion, (prev) => ({
                    ...prev,
                    choice,
                    eliminated: prev.eliminated.filter((item) => item !== choice),
                  }))
                }
                onToggleEliminate={(choice) =>
                  updateAnswerState(activeQuestion, (prev) => {
                    const already = prev.eliminated.includes(choice);
                    const nextEliminated = already
                      ? prev.eliminated.filter((item) => item !== choice)
                      : [...prev.eliminated, choice];
                    return {
                      ...prev,
                      choice: !already && prev.choice === choice ? null : prev.choice,
                      eliminated: nextEliminated,
                    };
                  })
                }
                onToggleClusterValue={(subKey: ClusterSubKey, value: boolean) =>
                  updateAnswerState(activeQuestion, (prev) => ({
                    ...prev,
                    cluster: {
                      ...prev.cluster,
                      [subKey]: prev.cluster[subKey] === value ? null : value,
                    },
                  }))
                }
                onChangeShortNumeric={(shortText) =>
                  updateAnswerState(activeQuestion, (prev) => ({
                    ...prev,
                    shortText,
                  }))
                }
                onToggleFlag={() =>
                  updateAnswerState(activeQuestion, (prev) => ({
                    ...prev,
                    flagged: !prev.flagged,
                  }))
                }
                onPrev={() => setActiveIndex((idx) => Math.max(0, idx - 1))}
                onNext={() => setActiveIndex((idx) => Math.min(questions.length - 1, idx + 1))}
                canPrev={activeIndex > 0}
                canNext={activeIndex < questions.length - 1}
              />
            )}
          </div>

          <div className="lg:col-span-4">
            <div className="lg:sticky lg:top-24">
              <ExamQuestionPalette
                questions={questions}
                answers={answers}
                activeQuestionIndex={activeIndex}
                onSelectQuestion={(idx) => setActiveIndex(idx)}
              />
            </div>
          </div>
        </main>

        {/* State 6: 409 Sequence Conflict Resolution Modal */}
        {effectiveUiState.kind === "SequenceConflictModal" && (
          <SequenceConflictModal
            conflict={
              activeConflict ?? {
                questionVersionId: activeQuestion?.questionVersionId ?? "unknown",
                questionLabel: activeQuestion ? `Câu ${activeQuestion.number}` : undefined,
                localPayload: activeQuestion
                  ? answers[activeQuestion.questionVersionId] ?? null
                  : null,
                serverPayload: null,
              }
            }
            onResolve={(choice) => void handleResolveConflict(choice)}
            isResolving={isResolvingConflict}
          />
        )}

        {/* Submission Confirmation & Receipt Modal */}
        <ExamSubmitModal
          open={submitModalOpen}
          totalQuestions={questions.length}
          answeredCount={stats.answered}
          partialPart2Count={stats.partial}
          flaggedCount={stats.flagged}
          submissionState={submissionState}
          onClose={() => setSubmitModalOpen(false)}
          onConfirmSubmit={() => void coordinator.submit("manual")}
          onRetrySubmit={() => void coordinator.onConnectionRestored()}
          onForceSubmitWithUnsynced={() =>
            void coordinator.submitWithUnsyncedAnswers("manual")
          }
        />
      </div>
    </ErrorBoundary>
  );
}
