export const FIVE_MINUTES_MS = 5 * 60 * 1000;
export const ONE_MINUTE_MS = 60 * 1000;

export type CountdownPhase = "normal" | "five_minute_warning" | "one_minute_warning" | "expired";

export interface CountdownTick {
  remainingMs: number;
  remainingSeconds: number;
  serverNowMs: number;
  deadlineMs: number;
  expired: boolean;
  phase: CountdownPhase;
}

export interface ClockSource {
  nowMs(): number;
}

export interface TimerScheduler {
  setInterval: (cb: () => void, ms: number) => ReturnType<typeof setInterval>;
  clearInterval: (id: ReturnType<typeof setInterval>) => void;
}

export interface ExamCountdownOptions {
  /** Authoritative ISO-8601 server deadline string or UTC epoch milliseconds. */
  deadlineAt: string | number;
  /** Authoritative server clock source (must implement `nowMs()`). */
  clock: ClockSource;
  /** Emitted on start, manual evaluation, and each interval tick while running. */
  onTick?: (state: CountdownTick) => void;
  /** Fired at most once when remaining time is <= 5 minutes (and > 0). */
  onFiveMinuteWarning?: (state: CountdownTick) => void;
  /** Fired at most once when remaining time is <= 1 minute (and > 0). */
  onOneMinuteWarning?: (state: CountdownTick) => void;
  /** Fired at most once when remaining time reaches 0. */
  onExpire?: (state: CountdownTick) => void;
  /** Poll cadence in milliseconds. Default: 1000ms. */
  tickIntervalMs?: number;
  /** Optional custom timer scheduler (defaults to globalThis setInterval/clearInterval). */
  scheduler?: TimerScheduler;
}

/**
 * Authoritative exam countdown computed from `(deadlineMs - clock.nowMs())`.
 *
 * Re-computes remaining time directly from the clock on every tick rather than decrementing
 * a counter, preventing drift across background tab throttling or OS sleep. Fires 5-minute,
 * 1-minute, and expiry notifications at most once each.
 */
export class ExamCountdown {
  private readonly deadlineMs: number;
  private readonly clock: ClockSource;
  private readonly onTick?: (state: CountdownTick) => void;
  private readonly onFiveMinuteWarning?: (state: CountdownTick) => void;
  private readonly onOneMinuteWarning?: (state: CountdownTick) => void;
  private readonly onExpire?: (state: CountdownTick) => void;
  private readonly tickIntervalMs: number;
  private readonly scheduler: TimerScheduler;

  private intervalId: ReturnType<typeof setInterval> | null = null;
  private firedFiveMin = false;
  private firedOneMin = false;
  private firedExpire = false;

  constructor(options: ExamCountdownOptions) {
    const parsedDeadline =
      typeof options.deadlineAt === "number" ? options.deadlineAt : Date.parse(options.deadlineAt);
    if (!Number.isFinite(parsedDeadline)) {
      throw new TypeError(`Invalid deadlineAt value: ${String(options.deadlineAt)}`);
    }
    const tickIntervalMs = options.tickIntervalMs ?? 1000;
    if (!Number.isFinite(tickIntervalMs) || tickIntervalMs <= 0) {
      throw new RangeError("tickIntervalMs must be a positive number");
    }

    this.deadlineMs = parsedDeadline;
    this.clock = options.clock;
    this.onTick = options.onTick;
    this.onFiveMinuteWarning = options.onFiveMinuteWarning;
    this.onOneMinuteWarning = options.onOneMinuteWarning;
    this.onExpire = options.onExpire;
    this.tickIntervalMs = tickIntervalMs;
    this.scheduler = options.scheduler ?? {
      setInterval: (cb, ms) => globalThis.setInterval(cb, ms),
      clearInterval: (id) => globalThis.clearInterval(id),
    };
  }

  get isRunning(): boolean {
    return this.intervalId !== null;
  }

  get isExpired(): boolean {
    return this.firedExpire;
  }

  /** Computes the current snapshot without triggering callbacks. */
  snapshot(): CountdownTick {
    const serverNowMs = this.clock.nowMs();
    const remainingMs = Math.max(0, this.deadlineMs - serverNowMs);
    const expired = remainingMs === 0;
    let phase: CountdownPhase = "normal";
    if (expired) {
      phase = "expired";
    } else if (remainingMs <= ONE_MINUTE_MS) {
      phase = "one_minute_warning";
    } else if (remainingMs <= FIVE_MINUTES_MS) {
      phase = "five_minute_warning";
    }
    return {
      remainingMs,
      remainingSeconds: Math.ceil(remainingMs / 1000),
      serverNowMs,
      deadlineMs: this.deadlineMs,
      expired,
      phase,
    };
  }

  /**
   * Starts emitting interval ticks. Performs an immediate evaluation first so late page loads
   * or already-expired deadlines update synchronously.
   */
  start(): CountdownTick {
    const state = this.evaluate();
    if (!state.expired && this.intervalId === null) {
      this.intervalId = this.scheduler.setInterval(() => {
        this.evaluate();
      }, this.tickIntervalMs);
    }
    return state;
  }

  /** Stops the interval timer without altering which warnings have already fired. */
  stop(): void {
    if (this.intervalId !== null) {
      this.scheduler.clearInterval(this.intervalId);
      this.intervalId = null;
    }
  }

  /**
   * Re-evaluates remaining time from the authoritative clock, fires any newly crossed threshold
   * callbacks at most once, emits `onTick`, and stops the interval once expired.
   */
  evaluate(): CountdownTick {
    const state = this.snapshot();

    if (state.expired) {
      this.stop();
      this.onTick?.(state);
      if (!this.firedExpire) {
        this.firedExpire = true;
        this.onExpire?.(state);
      }
      return state;
    }

    if (state.remainingMs <= FIVE_MINUTES_MS && !this.firedFiveMin) {
      this.firedFiveMin = true;
      this.onFiveMinuteWarning?.(state);
    }
    if (state.remainingMs <= ONE_MINUTE_MS && !this.firedOneMin) {
      this.firedOneMin = true;
      this.onOneMinuteWarning?.(state);
    }

    this.onTick?.(state);
    return state;
  }
}
