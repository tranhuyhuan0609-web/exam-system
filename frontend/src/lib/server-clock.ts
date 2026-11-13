export interface ClockDependencies {
  /** Wall-clock milliseconds (default: Date.now). Used only for wall-offset telemetry, never for elapsed time. */
  wallNowMs?: () => number;
  /** Monotonic milliseconds (default: performance.now). Anchors synchronized server time against OS clock tampering. */
  monotonicNowMs?: () => number;
  /** HTTP client (default: globalThis.fetch). */
  fetchFn?: typeof fetch;
}

export interface ClockSample {
  rttMs: number;
  serverNowMs: number;
  monotonicNowMs: number;
  wallOffsetMs: number;
  monotonicBiasMs: number;
}

export interface ServerTimePayload {
  server_now_ms?: number;
  server_time_ms?: number;
  server_time?: string;
  server_recv_ms?: number;
  server_send_ms?: number;
}

export class ServerClockSyncError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ServerClockSyncError";
    this.code = code;
  }
}

export interface ServerClockOptions extends ClockDependencies {
  /** Number of recent samples to inspect when selecting the minimum-RTT estimate. Default: 5. */
  windowSize?: number;
}

/**
 * Maintains an NTP-style estimate of authoritative server UTC epoch milliseconds.
 *
 * After synchronizing at monotonic time `m`, future reads compute `monotonicBiasMs + monotonicNowMs()`
 * using the sample with the lowest round-trip latency from the last `windowSize` (default 5) samples.
 * Because progression relies exclusively on the monotonic clock, mid-exam OS wall-clock changes do not
 * skew `nowMs()`.
 */
export class ServerClock {
  private readonly wallNowMs: () => number;
  private readonly monotonicNowMs: () => number;
  private readonly fetchFn: typeof fetch;
  private readonly windowSize: number;
  private readonly samples: ClockSample[] = [];

  constructor(options: ServerClockOptions = {}) {
    this.wallNowMs = options.wallNowMs ?? (() => Date.now());
    this.monotonicNowMs = options.monotonicNowMs ?? (() => performance.now());
    this.fetchFn = options.fetchFn ?? ((input, init) => globalThis.fetch(input, init));
    const windowSize = options.windowSize ?? 5;
    if (!Number.isInteger(windowSize) || windowSize < 1) {
      throw new RangeError("windowSize must be a positive integer");
    }
    this.windowSize = windowSize;
  }

  /** Number of retained samples in the sliding window. */
  get sampleCount(): number {
    return this.samples.length;
  }

  /** Whether at least one valid server clock sample has been recorded. */
  get isSynchronized(): boolean {
    return this.samples.length > 0;
  }

  /**
   * Returns current estimated server time in UTC epoch milliseconds,
   * or falls back to local wall clock if no sample has been recorded yet.
   */
  nowMs(): number {
    const best = this.bestSample();
    if (!best) {
      return this.wallNowMs();
    }
    return Math.round(best.monotonicBiasMs + this.monotonicNowMs());
  }

  /**
   * Returns estimated `server_ms - local_wall_ms` based on the current lowest-RTT sample,
   * or `0` if unsynchronized.
   */
  getOffsetMs(): number {
    const best = this.bestSample();
    return best ? Math.round(best.wallOffsetMs) : 0;
  }

  /** Returns the selected minimum-RTT sample from the active sliding window, if any. */
  bestSample(): ClockSample | null {
    if (this.samples.length === 0) {
      return null;
    }
    let best = this.samples[0]!;
    for (let i = 1; i < this.samples.length; i += 1) {
      const candidate = this.samples[i]!;
      // Prefer strictly lower RTT, or on equal RTT prefer the fresher sample.
      if (candidate.rttMs <= best.rttMs) {
        best = candidate;
      }
    }
    return best;
  }

  /**
   * Records one completed request/response sample into the sliding window.
   * Accepts either a single server timestamp (`t1ServerRecvMs`, assuming symmetric network delay)
   * or separate server receive/send timestamps (`t1ServerRecvMs`, `t2ServerSendMs`) per standard NTP.
   */
  recordSample(params: {
    t0MonotonicMs: number;
    t3MonotonicMs: number;
    t1ServerRecvMs: number;
    t2ServerSendMs?: number;
    clientWallAtRecvMs?: number;
  }): ClockSample {
    const { t0MonotonicMs, t3MonotonicMs, t1ServerRecvMs, t2ServerSendMs } = params;
    if (!Number.isFinite(t0MonotonicMs) || !Number.isFinite(t3MonotonicMs) || !Number.isFinite(t1ServerRecvMs)) {
      throw new ServerClockSyncError("INVALID_SAMPLE", "Clock sample timestamps must be finite numbers");
    }
    const serverSendMs = t2ServerSendMs ?? t1ServerRecvMs;
    if (!Number.isFinite(serverSendMs)) {
      throw new ServerClockSyncError("INVALID_SAMPLE", "Server send timestamp must be finite");
    }

    const totalClientElapsed = Math.max(0, t3MonotonicMs - t0MonotonicMs);
    const serverProcessing = Math.max(0, serverSendMs - t1ServerRecvMs);
    const networkRtt = Math.max(0, totalClientElapsed - serverProcessing);
    const estimatedServerAtRecv = serverSendMs + networkRtt / 2;

    const wallAtRecv = params.clientWallAtRecvMs ?? this.wallNowMs();
    const sample: ClockSample = {
      rttMs: networkRtt,
      serverNowMs: estimatedServerAtRecv,
      monotonicNowMs: t3MonotonicMs,
      wallOffsetMs: estimatedServerAtRecv - wallAtRecv,
      monotonicBiasMs: estimatedServerAtRecv - t3MonotonicMs,
    };

    this.samples.push(sample);
    if (this.samples.length > this.windowSize) {
      this.samples.shift();
    }
    return sample;
  }

  /**
   * Queries `url` to obtain an authoritative server timestamp and updates the sliding RTT window.
   */
  async sync(url: string, init?: RequestInit): Promise<ClockSample> {
    const t0MonotonicMs = this.monotonicNowMs();
    const response = await this.fetchFn(url, {
      method: init?.method ?? "GET",
      cache: "no-store",
      ...init,
    });
    const t3MonotonicMs = this.monotonicNowMs();
    const clientWallAtRecvMs = this.wallNowMs();

    if (!response.ok) {
      throw new ServerClockSyncError(
        "SYNC_HTTP_ERROR",
        `Server time endpoint returned HTTP ${response.status}`,
      );
    }

    const { recvMs, sendMs } = await extractServerTimestamps(response);
    return this.recordSample({
      t0MonotonicMs,
      t3MonotonicMs,
      t1ServerRecvMs: recvMs,
      t2ServerSendMs: sendMs,
      clientWallAtRecvMs,
    });
  }

  /** Clears all recorded samples. */
  reset(): void {
    this.samples.length = 0;
  }
}

async function extractServerTimestamps(response: Response): Promise<{ recvMs: number; sendMs?: number }> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const body = (await response.json()) as ServerTimePayload | null;
    if (body) {
      const recv =
        body.server_recv_ms ??
        body.server_now_ms ??
        body.server_time_ms ??
        (typeof body.server_time === "string" ? Date.parse(body.server_time) : Number.NaN);
      if (Number.isFinite(recv)) {
        const send =
          typeof body.server_send_ms === "number" && Number.isFinite(body.server_send_ms)
            ? body.server_send_ms
            : undefined;
        return { recvMs: recv, sendMs: send };
      }
    }
  }

  const dateHeader = response.headers.get("date");
  if (dateHeader) {
    const parsed = Date.parse(dateHeader);
    if (Number.isFinite(parsed)) {
      return { recvMs: parsed };
    }
  }

  throw new ServerClockSyncError(
    "MISSING_SERVER_TIME",
    "Response contained neither a JSON server timestamp nor a valid Date header",
  );
}
