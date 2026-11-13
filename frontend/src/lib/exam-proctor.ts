/**
 * Exam room Anti-Cheat telemetry accumulator & teacher flagging policy
 * (V11.3 Chapter 8.1 Workspace 2 Sprint 5 -> Sprint 6).
 *
 * Strictly advisory telemetry: debounces micro-flickers, never stores pasted
 * text PII, and never auto-terminates a student attempt client-side.
 */

export type ProctorEventKind =
  | "TAB_HIDDEN"
  | "TAB_VISIBLE"
  | "WINDOW_BLUR"
  | "FULLSCREEN_EXIT"
  | "PASTE_ATTEMPT"
  | "COPY_ATTEMPT";

export interface ProctorEventRecord {
  readonly kind: ProctorEventKind;
  readonly occurredAtMs: number;
  readonly durationMs?: number;
  readonly clipboardLength?: number;
}

export type ProctorRiskLevel = "LOW" | "MEDIUM" | "HIGH_REVIEW_REQUIRED";

export interface ProctorSummary {
  readonly hiddenSwitchCount: number;
  readonly totalHiddenMs: number;
  readonly fullscreenExitCount: number;
  readonly pasteAttemptCount: number;
  readonly copyAttemptCount: number;
  readonly riskLevel: ProctorRiskLevel;
  readonly flagForTeacherReview: boolean;
  readonly events: readonly ProctorEventRecord[];
}

export interface ProctorTrackerOptions {
  /** Brief focus flickers (< 300ms) from OS notifications are ignored. */
  readonly minHiddenDurationMs?: number;
  readonly maxStoredEvents?: number;
}

export class ExamProctorTracker {
  private readonly minHiddenMs: number;
  private readonly maxEvents: number;
  private readonly records: ProctorEventRecord[] = [];
  private hiddenSinceMs: number | null = null;
  private hiddenCount = 0;
  private totalHiddenDurationMs = 0;
  private fullscreenExits = 0;
  private pasteAttempts = 0;
  private copyAttempts = 0;

  constructor(options: ProctorTrackerOptions = {}) {
    this.minHiddenMs = options.minHiddenDurationMs ?? 300;
    this.maxEvents = options.maxStoredEvents ?? 250;
  }

  private pushEvent(ev: ProctorEventRecord): void {
    if (this.records.length < this.maxEvents) {
      this.records.push(ev);
    }
  }

  public recordVisibilityHidden(monotonicNowMs: number): void {
    if (this.hiddenSinceMs === null) {
      this.hiddenSinceMs = monotonicNowMs;
    }
  }

  public recordVisibilityVisible(monotonicNowMs: number): void {
    if (this.hiddenSinceMs === null) {
      return;
    }
    const elapsed = Math.max(0, monotonicNowMs - this.hiddenSinceMs);
    this.hiddenSinceMs = null;

    if (elapsed < this.minHiddenMs) {
      // Transient OS alert / micro-flicker below threshold: do not penalize.
      return;
    }
    this.hiddenCount += 1;
    this.totalHiddenDurationMs += elapsed;
    this.pushEvent({
      kind: "TAB_HIDDEN",
      occurredAtMs: monotonicNowMs - elapsed,
      durationMs: elapsed,
    });
    this.pushEvent({
      kind: "TAB_VISIBLE",
      occurredAtMs: monotonicNowMs,
      durationMs: elapsed,
    });
  }

  public recordFullscreenExit(monotonicNowMs: number): void {
    this.fullscreenExits += 1;
    this.pushEvent({ kind: "FULLSCREEN_EXIT", occurredAtMs: monotonicNowMs });
  }

  public recordClipboardAction(
    action: "PASTE" | "COPY",
    monotonicNowMs: number,
    textLength: number,
  ): void {
    const safeLen = Math.max(0, Math.floor(textLength));
    if (action === "PASTE") {
      this.pasteAttempts += 1;
      this.pushEvent({
        kind: "PASTE_ATTEMPT",
        occurredAtMs: monotonicNowMs,
        clipboardLength: safeLen,
      });
    } else {
      this.copyAttempts += 1;
      this.pushEvent({
        kind: "COPY_ATTEMPT",
        occurredAtMs: monotonicNowMs,
        clipboardLength: safeLen,
      });
    }
  }

  public getSummary(monotonicNowMs?: number): ProctorSummary {
    const openHiddenMs =
      this.hiddenSinceMs !== null && monotonicNowMs !== undefined
        ? Math.max(0, monotonicNowMs - this.hiddenSinceMs)
        : 0;
    const effectiveHiddenMs =
      this.totalHiddenDurationMs + (openHiddenMs >= this.minHiddenMs ? openHiddenMs : 0);

    let risk: ProctorRiskLevel = "LOW";
    if (
      this.hiddenCount >= 5 ||
      effectiveHiddenMs >= 60_000 ||
      this.pasteAttempts >= 3
    ) {
      risk = "HIGH_REVIEW_REQUIRED";
    } else if (
      this.hiddenCount >= 2 ||
      effectiveHiddenMs >= 10_000 ||
      this.fullscreenExits >= 2 ||
      this.pasteAttempts >= 1
    ) {
      risk = "MEDIUM";
    }

    return {
      hiddenSwitchCount: this.hiddenCount,
      totalHiddenMs: effectiveHiddenMs,
      fullscreenExitCount: this.fullscreenExits,
      pasteAttemptCount: this.pasteAttempts,
      copyAttemptCount: this.copyAttempts,
      riskLevel: risk,
      flagForTeacherReview: risk !== "LOW",
      events: [...this.records],
    };
  }
}
