import { describe, expect, it, vi } from "vitest";
import { ServerClock, ServerClockSyncError } from "../server-clock";

function createFakeClocks(initialWallMs: number, initialMonoMs = 1_000) {
  let wallMs = initialWallMs;
  let monoMs = initialMonoMs;
  return {
    wallNowMs: () => wallMs,
    monotonicNowMs: () => monoMs,
    advanceBoth(ms: number) {
      wallMs += ms;
      monoMs += ms;
    },
    tamperWallClock(deltaMs: number) {
      wallMs += deltaMs;
    },
    advanceMonotonic(ms: number) {
      monoMs += ms;
    },
  };
}

describe("ServerClock", () => {
  it("compensates when local clock is 10 minutes fast or 10 minutes slow", async () => {
    const serverStartMs = Date.UTC(2026, 10, 13, 10, 0, 0);
    const TEN_MIN = 10 * 60 * 1000;

    // Case 1: Local wall clock is 10 minutes FAST
    const fastClock = createFakeClocks(serverStartMs + TEN_MIN);
    const serverClockFast = new ServerClock({
      wallNowMs: fastClock.wallNowMs,
      monotonicNowMs: fastClock.monotonicNowMs,
      fetchFn: vi.fn(async () => {
        // Simulate 40ms total RTT
        fastClock.advanceBoth(40);
        return new Response(JSON.stringify({ server_now_ms: serverStartMs + 20 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    });

    await serverClockFast.sync("/api/time");
    expect(serverClockFast.isSynchronized).toBe(true);
    expect(serverClockFast.nowMs()).toBe(serverStartMs + 40);
    expect(serverClockFast.getOffsetMs()).toBe(-TEN_MIN);

    // Case 2: Local wall clock is 10 minutes SLOW
    const slowClock = createFakeClocks(serverStartMs - TEN_MIN);
    const serverClockSlow = new ServerClock({
      wallNowMs: slowClock.wallNowMs,
      monotonicNowMs: slowClock.monotonicNowMs,
      fetchFn: vi.fn(async () => {
        slowClock.advanceBoth(60);
        return new Response(JSON.stringify({ server_now_ms: serverStartMs + 30 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    });

    await serverClockSlow.sync("/api/time");
    expect(serverClockSlow.nowMs()).toBe(serverStartMs + 60);
    expect(serverClockSlow.getOffsetMs()).toBe(TEN_MIN);
  });

  it("ignores mid-exam OS wall clock changes by anchoring progression to monotonic clock", async () => {
    const serverStartMs = Date.UTC(2026, 10, 13, 10, 0, 0);
    const clocks = createFakeClocks(serverStartMs, 5_000);

    const serverClock = new ServerClock({
      wallNowMs: clocks.wallNowMs,
      monotonicNowMs: clocks.monotonicNowMs,
    });

    clocks.advanceMonotonic(20);
    serverClock.recordSample({
      t0MonotonicMs: 5_000,
      t3MonotonicMs: 5_020,
      t1ServerRecvMs: serverStartMs + 10,
      clientWallAtRecvMs: serverStartMs + 20,
    });

    expect(serverClock.nowMs()).toBe(serverStartMs + 20);

    // OS user rewinds wall clock by 3 hours while 15 seconds elapse monotonically
    clocks.tamperWallClock(-3 * 60 * 60 * 1000);
    clocks.advanceMonotonic(15_000);

    expect(serverClock.nowMs()).toBe(serverStartMs + 20 + 15_000);

    // OS user pushes wall clock forward by 5 hours
    clocks.tamperWallClock(5 * 60 * 60 * 1000);
    expect(serverClock.nowMs()).toBe(serverStartMs + 20 + 15_000);
  });

  it("selects the sample with lowest RTT among the last 5 samples and evicts stale ones", () => {
    const baseServerMs = 1_700_000_000_000;
    let mono = 10_000;
    const clock = new ServerClock({
      wallNowMs: () => baseServerMs,
      monotonicNowMs: () => mono,
    });

    // Record 5 samples with varying RTTs (lowest is index 1 with RTT=12ms)
    clock.recordSample({ t0MonotonicMs: 1_000, t3MonotonicMs: 1_100, t1ServerRecvMs: baseServerMs + 50 }); // RTT 100
    clock.recordSample({ t0MonotonicMs: 2_000, t3MonotonicMs: 2_012, t1ServerRecvMs: baseServerMs + 1_006 }); // RTT 12 (best)
    clock.recordSample({ t0MonotonicMs: 3_000, t3MonotonicMs: 3_080, t1ServerRecvMs: baseServerMs + 2_040 }); // RTT 80
    clock.recordSample({ t0MonotonicMs: 4_000, t3MonotonicMs: 4_040, t1ServerRecvMs: baseServerMs + 3_020 }); // RTT 40
    clock.recordSample({ t0MonotonicMs: 5_000, t3MonotonicMs: 5_060, t1ServerRecvMs: baseServerMs + 4_030 }); // RTT 60

    expect(clock.sampleCount).toBe(5);
    expect(clock.bestSample()?.rttMs).toBe(12);

    // Pushing 2 more samples evicts the RTT=100 and RTT=12 samples from the 5-sample window;
    // the new lowest RTT in the last 5 becomes RTT=24ms.
    clock.recordSample({ t0MonotonicMs: 6_000, t3MonotonicMs: 6_050, t1ServerRecvMs: baseServerMs + 5_025 }); // RTT 50
    clock.recordSample({ t0MonotonicMs: 7_000, t3MonotonicMs: 7_024, t1ServerRecvMs: baseServerMs + 6_012 }); // RTT 24 (new best)

    expect(clock.sampleCount).toBe(5);
    expect(clock.bestSample()?.rttMs).toBe(24);
    mono = 7_024;
    expect(clock.nowMs()).toBe(baseServerMs + 6_024);
  });

  it("supports HTTP Date header fallback and throws ServerClockSyncError on bad responses", async () => {
    const serverDate = new Date("2026-11-13T10:15:30.000Z");
    let mono = 100;
    const clock = new ServerClock({
      wallNowMs: () => 0,
      monotonicNowMs: () => mono,
      fetchFn: async () => {
        mono += 20;
        return new Response("", {
          status: 200,
          headers: { date: serverDate.toUTCString() },
        });
      },
    });

    const sample = await clock.sync("/api/ping");
    expect(sample.rttMs).toBe(20);
    expect(clock.nowMs()).toBe(serverDate.getTime() + 10);

    const brokenClock = new ServerClock({
      fetchFn: async () => new Response("error", { status: 503 }),
    });
    await expect(brokenClock.sync("/api/ping")).rejects.toBeInstanceOf(ServerClockSyncError);

    const missingClock = new ServerClock({
      fetchFn: async () => new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    });
    await expect(missingClock.sync("/api/ping")).rejects.toThrow(/MISSING_SERVER_TIME|Response contained neither/);
  });
});
