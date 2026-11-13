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
}

export interface SubmitAttemptRequest {
  attemptId: string;
  submitIdempotencyKey: string;
  trigger: SubmissionTrigger;
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
}

export interface ExamSubmissionCoordinatorOptions {
  attemptId: string;
  flushPendingAutosaves: () => Promise<AutosaveFlushOutcome>;
  submitAttempt: (req: SubmitAttemptRequest) => Promise<SubmitAttemptResponse>;
  createIdempotencyKey?: (attemptId: string) => string;
  retryDelaysMs?: readonly number[];
  onStateChange?: (state: SubmissionStateSnapshot) => void;
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
    };
  }

  getSnapshot(): SubmissionStateSnapshot {
    return { ...this.state };
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
    return this.retryDelaysMs[idx] ?? 2_000;
  }

  /**
   * Execute or join an active submission flow. Safe to call simultaneously from manual click + auto-expire.
   */
  async submit(trigger: SubmissionTrigger): Promise<SubmissionStateSnapshot> {
    if (this.state.phase === "submitted") {
      return this.getSnapshot();
    }
    if (this.inFlightPromise) {
      return this.inFlightPromise;
    }

    const idempotencyKey =
      this.state.submitIdempotencyKey ?? this.createIdempotencyKey(this.attemptId);

    const run = (async (): Promise<SubmissionStateSnapshot> => {
      this.transition({
        phase: "flushing_autosave",
        inputLocked: this.state.inputLocked || trigger === "auto_expire" || trigger === "manual",
        trigger,
        submitIdempotencyKey: idempotencyKey,
        errorCode: null,
        errorMessage: null,
      });

      let flushOutcome: AutosaveFlushOutcome;
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

      if (flushOutcome.status === "auth_required" || flushOutcome.status === "rejected") {
        return this.transition({
          phase: "rejected",
          inputLocked: trigger === "auto_expire",
          nextRetryDelayMs: null,
          pendingCount: flushOutcome.remainingPendingCount,
          errorCode:
            flushOutcome.status === "auth_required" ? "AUTH_REQUIRED" : "AUTOSAVE_REJECTED",
          errorMessage: flushOutcome.errorMessage ?? "Submission blocked by rejected autosave batch",
        });
      }

      if (flushOutcome.status === "offline" || flushOutcome.remainingPendingCount > 0) {
        const retryCount = this.state.retryCount + 1;
        return this.transition({
          phase: "offline_retry_banner",
          retryCount,
          nextRetryDelayMs: this.nextBackoffDelay(retryCount),
          pendingCount: flushOutcome.remainingPendingCount,
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
        submitResp = await this.submitAttempt({
          attemptId: this.attemptId,
          submitIdempotencyKey: idempotencyKey,
          trigger,
        });
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
        inputLocked: trigger === "auto_expire",
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
