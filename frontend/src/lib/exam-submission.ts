export type SubmissionTrigger = "manual" | "auto_expire" | "retry";

export type SubmissionPhase =
  | "idle"
  | "flushing_autosave"
  | "submitting"
  | "offline_retry_banner"
  | "sequence_conflict"
  | "submitted"
  | "rejected";

export interface AutosaveFlushOutcome {
  flushedCount: number;
  remainingPendingCount: number;
  status: "ok" | "offline" | "auth_required" | "rejected";
  errorMessage?: string;
  /**
   * Question ids whose local answers the server permanently rejected (only meaningful for
   * `status: "rejected"`). Surfaced to the UI and forwarded with a forced submit so that nothing
   * is dropped silently.
   */
  unsyncedAnswerIds?: readonly string[];
}

export interface SubmitAttemptRequest {
  attemptId: string;
  submitIdempotencyKey: string;
  /** Original trigger of the submission (`manual` or `auto_expire`); never `retry`. */
  trigger: SubmissionTrigger;
  /** True only when sent via `submitWithUnsyncedAnswers` (autosave batch permanently rejected). */
  forcedWithUnsyncedAnswers?: boolean;
  /** Local answers that never reached the server; present only for a forced submit. */
  unsyncedAnswerIds?: readonly string[];
}

export interface SubmitAttemptResponse {
  status: "submitted" | "already_submitted" | "retryable_error" | "sequence_conflict" | "rejected";
  submittedAtServerIso?: string;
  receiptId?: string;
  errorCode?: string;
  errorMessage?: string;
}

export interface SubmissionStateSnapshot {
  attemptId: string;
  phase: SubmissionPhase;
  inputLocked: boolean;
  trigger: SubmissionTrigger | null;
  submitIdempotencyKey: string | null;
  retryCount: number;
  nextRetryDelayMs: number | null;
  pendingCount: number;
  receiptId: string | null;
  submittedAtServerIso: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  /** Answers the server rejected permanently (empty unless `canSubmitWithUnsynced`). */
  unsyncedAnswerIds: readonly string[];
  /**
   * True when the autosave batch was permanently rejected (`AUTOSAVE_REJECTED`): the UI should show
   * the unsynced answers and offer `submitWithUnsyncedAnswers()` as the explicit way out.
   */
  canSubmitWithUnsynced: boolean;
}

export interface ExamSubmissionCoordinatorOptions {
  attemptId: string;
  flushPendingAutosaves: () => Promise<AutosaveFlushOutcome>;
  submitAttempt: (req: SubmitAttemptRequest) => Promise<SubmitAttemptResponse>;
  createIdempotencyKey?: (attemptId: string) => string;
  retryDelaysMs?: readonly number[];
  onStateChange?: (state: SubmissionStateSnapshot) => void;
}

function sanitizeCount(value: number): number {
  return Number.isFinite(value) && value >= 0 ? Math.floor(value) : 1;
}

const DEFAULT_RETRY_DELAYS_MS: readonly number[] = [1_000, 2_000, 5_000, 10_000, 15_000];

/**
 * Resilient Workspace-2 exam submission coordinator (C05/C06).
 *
 * Enforces:
 * - Single-flight submission dedup with one stable `submitIdempotencyKey` reused across retries.
 * - Mandatory autosave flush barrier before final `/submit`.
 * - Immediate `inputLocked = true` on `auto_expire` even during transient network outages, surfacing
 *   `offline_retry_banner` / `sequence_conflict` cleanly without losing unsynced local responses.
 * - Once the exam expired, input stays locked for good (even after a rejection); the original trigger
 *   (`manual` / `auto_expire`) is sent on every retry, `retry` is never forwarded.
 * - One submission = one idempotency key, reused for every retry from the offline banner. After a
 *   terminal outcome (`rejected` / `sequence_conflict`) the next call starts a NEW submission with a
 *   fresh, never-before-used key.
 * - Way out of a permanently rejected autosave batch: phase `rejected` + `errorCode: AUTOSAVE_REJECTED`
 *   + `canSubmitWithUnsynced: true` + `unsyncedAnswerIds`; the UI then calls
 *   `submitWithUnsyncedAnswers()`, which skips the flush barrier, sends the list of unsynced answers
 *   with the request, and goes through the same single-flight guard (no double submit).
 */
export class ExamSubmissionCoordinator {
  private readonly attemptId: string;
  private readonly flushPendingAutosaves: () => Promise<AutosaveFlushOutcome>;
  private readonly submitAttempt: (req: SubmitAttemptRequest) => Promise<SubmitAttemptResponse>;
  private readonly createIdempotencyKey: (attemptId: string) => string;
  private readonly retryDelaysMs: readonly number[];
  private readonly onStateChange?: (state: SubmissionStateSnapshot) => void;

  private state: SubmissionStateSnapshot;
  private inFlightPromise: Promise<SubmissionStateSnapshot> | null = null;
  private counter = 0;
  private expired = false;
  private effectiveTrigger: "manual" | "auto_expire" | null = null;
  private readonly usedKeys = new Set<string>();

  constructor(options: ExamSubmissionCoordinatorOptions) {
    if (!options.attemptId) {
      throw new TypeError("attemptId must be non-empty");
    }
    this.attemptId = options.attemptId;
    this.flushPendingAutosaves = options.flushPendingAutosaves;
    this.submitAttempt = options.submitAttempt;
    this.createIdempotencyKey =
      options.createIdempotencyKey ??
      ((id) => {
        this.counter += 1;
        return `submit:${id}:${this.counter}`;
      });
    this.retryDelaysMs = options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    this.onStateChange = options.onStateChange;

    this.state = {
      attemptId: options.attemptId,
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
    };
  }

  getSnapshot(): SubmissionStateSnapshot {
    return { ...this.state, unsyncedAnswerIds: [...this.state.unsyncedAnswerIds] };
  }

  private transition(patch: Partial<SubmissionStateSnapshot>): SubmissionStateSnapshot {
    this.state = { ...this.state, ...patch };
    this.onStateChange?.(this.getSnapshot());
    return this.getSnapshot();
  }

  private nextBackoffDelay(retryCount: number): number {
    if (this.retryDelaysMs.length === 0) {
      return 2_000;
    }
    const idx = Math.min(retryCount - 1, this.retryDelaysMs.length - 1);
    const delay = this.retryDelaysMs[idx];
    return delay !== undefined && Number.isFinite(delay) && delay >= 0 ? delay : 2_000;
  }

  private freshKey(): string {
    const base = this.createIdempotencyKey(this.attemptId);
    let key = base;
    let n = 1;
    while (this.usedKeys.has(key)) {
      n += 1;
      key = `${base}#${n}`;
    }
    this.usedKeys.add(key);
    return key;
  }

  private markExpired(): void {
    this.expired = true;
    this.effectiveTrigger = "auto_expire";
    if (!this.state.inputLocked || this.state.trigger !== "auto_expire") {
      this.transition({ inputLocked: true, trigger: "auto_expire" });
    }
  }

  /**
   * Execute or join an active submission flow. Safe to call simultaneously from manual click + auto-expire:
   * concurrent callers share ONE in-flight submission (one network request).
   */
  async submit(trigger: SubmissionTrigger): Promise<SubmissionStateSnapshot> {
    if (this.state.phase === "submitted") {
      return this.getSnapshot();
    }
    if (this.inFlightPromise) {
      if (trigger === "auto_expire") {
        this.markExpired();
      }
      return this.inFlightPromise;
    }
    return this.start(trigger, false);
  }

  /**
   * Explicit way out of a permanently rejected autosave batch (`phase: "rejected"`,
   * `errorCode: "AUTOSAVE_REJECTED"`, `canSubmitWithUnsynced: true`). Skips the flush barrier and submits
   * with the answers that could not be synced listed in `unsyncedAnswerIds` of the request, so nothing
   * is dropped silently. Joins an in-flight submission instead of starting a second one.
   *
   * @throws Error when the coordinator is not in the rejected-autosave state.
   */
  async submitWithUnsyncedAnswers(
    trigger: Exclude<SubmissionTrigger, "retry"> = "manual",
  ): Promise<SubmissionStateSnapshot> {
    if (this.state.phase === "submitted") {
      return this.getSnapshot();
    }
    if (this.inFlightPromise) {
      if (trigger === "auto_expire") {
        this.markExpired();
      }
      return this.inFlightPromise;
    }
    if (!this.state.canSubmitWithUnsynced) {
      throw new Error("submitWithUnsyncedAnswers requires a permanently rejected autosave batch");
    }
    return this.start(trigger, true);
  }

  private async start(trigger: SubmissionTrigger, forced: boolean): Promise<SubmissionStateSnapshot> {
    // Same submission only while paused on the offline banner (or when forcing after a rejected
    // flush, whose key never reached the network). Everything else is a new submission.
    const continuing = forced || this.state.phase === "offline_retry_banner";
    const idempotencyKey =
      continuing && this.state.submitIdempotencyKey !== null
        ? this.state.submitIdempotencyKey
        : this.freshKey();
    if (trigger === "auto_expire") {
      this.expired = true;
    }
    const original = continuing ? this.effectiveTrigger : null;
    this.effectiveTrigger = this.expired
      ? "auto_expire"
      : (original ?? (trigger === "retry" ? "manual" : trigger));
    const effective = this.effectiveTrigger;
    const unsyncedIds = forced ? [...this.state.unsyncedAnswerIds] : [];

    const run = (async (): Promise<SubmissionStateSnapshot> => {
      this.transition({
        phase: "flushing_autosave",
        inputLocked: true,
        trigger: effective,
        submitIdempotencyKey: idempotencyKey,
        retryCount: continuing ? this.state.retryCount : 0,
        errorCode: null,
        errorMessage: null,
        canSubmitWithUnsynced: false,
        unsyncedAnswerIds: unsyncedIds,
      });

      let flushOutcome: AutosaveFlushOutcome = { flushedCount: 0, remainingPendingCount: 0, status: "ok" };
      if (!forced) {
        try {
          flushOutcome = await this.flushPendingAutosaves();
        } catch (err) {
          const retryCount = this.state.retryCount + 1;
          return this.transition({
            phase: "offline_retry_banner",
            retryCount,
            nextRetryDelayMs: this.nextBackoffDelay(retryCount),
            errorCode: "AUTOSAVE_FLUSH_OFFLINE",
            errorMessage: err instanceof Error ? err.message : "Network offline during autosave flush",
          });
        }
      }

      if (flushOutcome.status === "auth_required" || flushOutcome.status === "rejected") {
        const rejectedBatch = flushOutcome.status === "rejected";
        return this.transition({
          phase: "rejected",
          inputLocked: this.expired,
          nextRetryDelayMs: null,
          pendingCount: sanitizeCount(flushOutcome.remainingPendingCount),
          unsyncedAnswerIds: rejectedBatch ? [...(flushOutcome.unsyncedAnswerIds ?? [])] : [],
          canSubmitWithUnsynced: rejectedBatch,
          errorCode: rejectedBatch ? "AUTOSAVE_REJECTED" : "AUTH_REQUIRED",
          errorMessage: flushOutcome.errorMessage ?? "Submission blocked by rejected autosave batch",
        });
      }

      // Fail closed: anything but a clean "ok" with a valid zero pending count keeps the barrier shut.
      if (
        flushOutcome.status !== "ok" ||
        !Number.isFinite(flushOutcome.remainingPendingCount) ||
        flushOutcome.remainingPendingCount !== 0
      ) {
        const retryCount = this.state.retryCount + 1;
        return this.transition({
          phase: "offline_retry_banner",
          retryCount,
          nextRetryDelayMs: this.nextBackoffDelay(retryCount),
          pendingCount: sanitizeCount(flushOutcome.remainingPendingCount),
          errorCode: "AUTOSAVE_PENDING_OFFLINE",
          errorMessage: flushOutcome.errorMessage ?? "Pending responses awaiting network connection",
        });
      }

      this.transition({
        phase: "submitting",
        pendingCount: 0,
      });

      let submitResp: SubmitAttemptResponse;
      try {
        const request: SubmitAttemptRequest = {
          attemptId: this.attemptId,
          submitIdempotencyKey: idempotencyKey,
          trigger: this.effectiveTrigger ?? effective,
        };
        if (forced) {
          request.forcedWithUnsyncedAnswers = true;
          request.unsyncedAnswerIds = unsyncedIds;
        }
        submitResp = await this.submitAttempt(request);
      } catch (err) {
        const retryCount = this.state.retryCount + 1;
        return this.transition({
          phase: "offline_retry_banner",
          retryCount,
          nextRetryDelayMs: this.nextBackoffDelay(retryCount),
          errorCode: "SUBMIT_NETWORK_ERROR",
          errorMessage: err instanceof Error ? err.message : "Network error submitting attempt",
        });
      }

      if (submitResp.status === "submitted" || submitResp.status === "already_submitted") {
        return this.transition({
          phase: "submitted",
          inputLocked: true,
          nextRetryDelayMs: null,
          pendingCount: 0,
          receiptId: submitResp.receiptId ?? idempotencyKey,
          submittedAtServerIso: submitResp.submittedAtServerIso ?? null,
          errorCode: null,
          errorMessage: null,
        });
      }

      if (submitResp.status === "sequence_conflict") {
        return this.transition({
          phase: "sequence_conflict",
          nextRetryDelayMs: null,
          errorCode: submitResp.errorCode ?? "STALE_CLIENT_SEQUENCE",
          errorMessage: submitResp.errorMessage ?? "Response sequence conflict detected with server",
        });
      }

      if (submitResp.status === "retryable_error") {
        const retryCount = this.state.retryCount + 1;
        return this.transition({
          phase: "offline_retry_banner",
          retryCount,
          nextRetryDelayMs: this.nextBackoffDelay(retryCount),
          errorCode: submitResp.errorCode ?? "SUBMIT_RETRYABLE",
          errorMessage: submitResp.errorMessage ?? "Temporary server error during submission",
        });
      }

      return this.transition({
        phase: "rejected",
        inputLocked: this.expired,
        nextRetryDelayMs: null,
        errorCode: submitResp.errorCode ?? "SUBMIT_REJECTED",
        errorMessage: submitResp.errorMessage ?? "Submission rejected by server",
      });
    })();

    this.inFlightPromise = run;
    try {
      return await run;
    } finally {
      this.inFlightPromise = null;
    }
  }

  /** Trigger recovery retry when the browser regains connectivity while paused on offline banner. */
  async onConnectionRestored(): Promise<SubmissionStateSnapshot> {
    if (this.state.phase === "offline_retry_banner") {
      return this.submit("retry");
    }
    return this.getSnapshot();
  }
}
