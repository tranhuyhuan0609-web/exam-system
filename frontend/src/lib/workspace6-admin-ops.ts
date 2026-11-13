/**
 * Workspace 6 (Admin Ops, Google Drive Picker, ParserDriftMonitor & Takedown Console)
 * pure policy helpers per V11.3 Chapters 3 & 9.
 */

export type CriticalAdminAction =
  | "EXECUTE_TAKEDOWN"
  | "OVERRIDE_GRADE"
  | "CHANGE_RIGHTS_STATUS"
  | "REPLAY_DLQ";

export const MIN_ADMIN_AUDIT_REASON_CHARS = 15;

/** Zero-width, bidi-control, BOM, soft-hyphen and similar invisible characters. */
const INVISIBLE_CHARS_RE = /[\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF\u00AD\u180E\u034F]/g;

export interface AdminReasonValidation {
  readonly ok: boolean;
  readonly action: CriticalAdminAction;
  readonly normalizedReason: string;
  readonly charLength: number;
  readonly errorCode: "REASON_REQUIRED" | "REASON_TOO_SHORT" | null;
}

/**
 * Every sensitive admin button (`Execute Takedown`, `Override Grade`,
 * `Change Rights Status`, `Replay DLQ`) requires a non-trivial justification
 * (>= 15 characters) persisted to `audit_events`.
 */
export function validateAdminMutationReason(
  action: CriticalAdminAction,
  rawReason: string,
): AdminReasonValidation {
  const normalizedReason = (typeof rawReason === "string" ? rawReason : "")
    .replace(INVISIBLE_CHARS_RE, "")
    .trim()
    .replace(/\s+/g, " ");
  const charLength = Array.from(normalizedReason).length;

  if (charLength === 0) {
    return {
      ok: false,
      action,
      normalizedReason,
      charLength,
      errorCode: "REASON_REQUIRED",
    };
  }
  if (charLength < MIN_ADMIN_AUDIT_REASON_CHARS) {
    return {
      ok: false,
      action,
      normalizedReason,
      charLength,
      errorCode: "REASON_TOO_SHORT",
    };
  }
  return {
    ok: true,
    action,
    normalizedReason,
    charLength,
    errorCode: null,
  };
}

export interface DrivePairingSignalsInput {
  readonly filenameSimilarity: number; // 0.0 .. 1.0
  readonly sameFolder: boolean;
  readonly titleOverlap: number; // 0.0 .. 1.0
  readonly examQuestionCount: number;
  readonly keyQuestionCount: number;
}

export interface DrivePairingEvaluation {
  readonly confidencePct: number;
  readonly canConfirm: boolean;
  readonly highConfidence: boolean;
  readonly warningCodes: readonly string[];
}

function clampUnit(value: number): number {
  if (!Number.isFinite(value) || value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/**
 * Evaluates 4-signal `Đề + HDC` pairing confidence in Workspace 6 Google Picker UI
 * (`filename`, `sameFolder`, `title`, `questionCount`).
 */
export function evaluateDrivePairingCandidate(
  signals: DrivePairingSignalsInput,
): DrivePairingEvaluation {
  const warnings: string[] = [];
  const fnScore = clampUnit(signals.filenameSimilarity);
  const folderScore = signals.sameFolder ? 1 : 0;
  const titleScore = clampUnit(signals.titleOverlap);

  let countScore = 0;
  const countsValid =
    Number.isFinite(signals.examQuestionCount) &&
    Number.isFinite(signals.keyQuestionCount) &&
    signals.examQuestionCount > 0 &&
    signals.keyQuestionCount > 0;
  if (countsValid) {
    const maxCount = Math.max(signals.examQuestionCount, signals.keyQuestionCount);
    const diff = Math.abs(signals.examQuestionCount - signals.keyQuestionCount);
    countScore = Math.max(0, 1 - diff / maxCount);
    if (diff > 0) {
      warnings.push("QUESTION_COUNT_MISMATCH");
    }
  } else {
    warnings.push("QUESTION_COUNT_MISSING");
  }

  if (!signals.sameFolder) {
    warnings.push("DIFFERENT_FOLDER");
  }

  const weighted =
    fnScore * 0.35 + folderScore * 0.25 + titleScore * 0.2 + countScore * 0.2;
  const exactPct = weighted * 100;
  const confidencePct = Math.round(exactPct);
  // Thresholds use the unrounded value (1e-9 only absorbs floating-point noise of the weighted sum).
  const EPS = 1e-9;

  return {
    confidencePct,
    // A different number of questions in exam and answer key can never be confirmed.
    canConfirm: exactPct + EPS >= 50 && !warnings.includes("QUESTION_COUNT_MISMATCH"),
    highConfidence: exactPct + EPS >= 85 && warnings.length === 0,
    warningCodes: warnings,
  };
}

export interface ParserDriftStatus {
  readonly status: "HEALTHY" | "WATCH" | "PAUSED_DRIFT";
  readonly emptyRatePct: number;
  readonly shouldAutoPause: boolean;
}

/**
 * Module A02: transitions crawler source to `PAUSED_DRIFT` when empty extraction rate
 * exceeds 15% across 30 or more consecutive pages.
 */
export function evaluateParserDriftStatus(
  emptyPageCount: number,
  consecutivePages: number,
): ParserDriftStatus {
  if (!Number.isFinite(emptyPageCount) || !Number.isFinite(consecutivePages)) {
    // Fail closed: unknown telemetry is never reported as healthy (but does not auto-pause on garbage).
    return { status: "WATCH", emptyRatePct: 100, shouldAutoPause: false };
  }
  if (consecutivePages <= 0) {
    return { status: "HEALTHY", emptyRatePct: 0, shouldAutoPause: false };
  }
  const safeEmpty = Math.max(0, Math.min(emptyPageCount, consecutivePages));
  const rate = safeEmpty / consecutivePages;
  const emptyRatePct = Math.round(rate * 1000) / 10;

  if (consecutivePages >= 30 && rate > 0.15) {
    return {
      status: "PAUSED_DRIFT",
      emptyRatePct,
      shouldAutoPause: true,
    };
  }
  if (rate > 0.15) {
    return {
      status: "WATCH",
      emptyRatePct,
      shouldAutoPause: false,
    };
  }
  return {
    status: "HEALTHY",
    emptyRatePct,
    shouldAutoPause: false,
  };
}

export interface OutboxSloStatus {
  readonly status: "OK" | "SLO_BREACH";
  readonly oldestQueuedAgeSeconds: number;
  readonly dlqSize: number;
  readonly alertCodes: readonly string[];
}

/**
 * Chapter 9.2 SLO: ingestion/outbox oldest queued job age must stay <= 90s.
 */
export function evaluateOutboxQueueHealth(
  oldestQueuedAgeSeconds: number,
  dlqSize: number,
): OutboxSloStatus {
  const alerts: string[] = [];
  if (!Number.isFinite(oldestQueuedAgeSeconds)) {
    // Fail closed: an unknown queue age is a breach.
    alerts.push("INGESTION_QUEUE_AGE_UNKNOWN", "INGESTION_QUEUE_SLO_BREACH");
  } else if (oldestQueuedAgeSeconds > 90) {
    alerts.push("INGESTION_QUEUE_SLO_BREACH");
  }
  if (!Number.isFinite(dlqSize) || dlqSize > 0) {
    alerts.push("DLQ_NON_EMPTY");
  }
  return {
    status: alerts.includes("INGESTION_QUEUE_SLO_BREACH") ? "SLO_BREACH" : "OK",
    oldestQueuedAgeSeconds,
    dlqSize,
    alertCodes: alerts,
  };
}
