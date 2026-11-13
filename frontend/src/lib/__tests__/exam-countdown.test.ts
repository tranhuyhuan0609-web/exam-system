import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExamCountdown, FIVE_MINUTES_MS, ONE_MINUTE_MS } from "../exam-countdown";
import { ServerClock } from "../server-clock";

describe("ExamCountdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits 1s ticks and fires 5-minute, 1-minute, and expiry callbacks at most once each", () => {
    const serverStartMs = Date.UTC(2026, 10, 13, 12, 0, 0);
    const deadlineMs = serverStartMs + FIVE_MINUTES_MS + 3_000; // 5m 03s left
    let mono = 0;

    const clock = new ServerClock({
      wallNowMs: () => serverStartMs + mono,
      monotonicNowMs: () => mono,
    });
    clock.recordSample({
      t0MonotonicMs: 0,
      t3MonotonicMs: 0,
      t1ServerRecvMs: serverStartMs,
      clientWallAtRecvMs: serverStartMs,
    });

    const ticks: number[] = [];
    const onFive = vi.fn();
    const onOne = vi.fn();
    const onExpire = vi.fn();

    const scheduler = {
      setInterval: (cb: () => void, ms: number) =>
        globalThis.setInterval(() => {
          cb();
        }, ms),
      clearInterval: (id: ReturnType<typeof setInterval>) => globalThis.clearInterval(id),
    };

    const countdown = new ExamCountdown({
      deadlineAt: new Date(deadlineMs).toISOString(),
      clock,
      tickIntervalMs: 1_000,
      scheduler,
      onTick: (s) => ticks.push(s.remainingSeconds),
      onFiveMinuteWarning: onFive,
      onOneMinuteWarning: onOne,
      onExpire,
    });

    const stepMs = (ms: number) => {
      for (let elapsed = 0; elapsed < ms; elapsed += 1_000) {
        mono += 1_000;
        vi.advanceTimersByTime(1_000);
      }
    };

    const initial = countdown.start();
    expect(initial.remainingSeconds).toBe(303);
    expect(initial.phase).toBe("normal");
    expect(onFive).not.toHaveBeenCalled();

    // Advance 2s -> 301s left (still > 5m)
    stepMs(2_000);
    expect(onFive).not.toHaveBeenCalled();

    // Cross 300s (5m warning fires once)
    stepMs(5_000);
    expect(onFive).toHaveBeenCalledTimes(1);
    expect(onFive.mock.calls[0]?.[0]?.phase).toBe("five_minute_warning");

    // Advance well into 1-minute warning window (and verify 5m never re-fires)
    stepMs(240_000);
    expect(onFive).toHaveBeenCalledTimes(1);
    expect(onOne).toHaveBeenCalledTimes(1);
    expect(onOne.mock.calls[0]?.[0]?.phase).toBe("one_minute_warning");

    // Finish countdown past deadline
    stepMs(60_000);
    expect(onExpire).toHaveBeenCalledTimes(1);
    expect(onExpire.mock.calls[0]?.[0]?.remainingMs).toBe(0);
    expect(countdown.isExpired).toBe(true);
    expect(countdown.isRunning).toBe(false);

    // Further manual evaluations or timer advances cannot re-fire any warning or expire
    mono += 10_000;
    vi.advanceTimersByTime(10_000);
    countdown.evaluate();
    expect(onFive).toHaveBeenCalledTimes(1);
    expect(onOne).toHaveBeenCalledTimes(1);
    expect(onExpire).toHaveBeenCalledTimes(1);
    expect(ticks[0]).toBe(303);
  });

  it("stays accurate across a 3-minute tab sleep and local clock tampering", () => {
    const serverStartMs = Date.UTC(2026, 10, 13, 14, 0, 0);
    // Exam starts 10 minutes slow on the OS clock!
    let osWallMs = serverStartMs - 10 * 60 * 1000;
    let monoMs = 10_000;

    const clock = new ServerClock({
      wallNowMs: () => osWallMs,
      monotonicNowMs: () => monoMs,
    });
    clock.recordSample({
      t0MonotonicMs: 9_980,
      t3MonotonicMs: 10_000,
      t1ServerRecvMs: serverStartMs - 10,
      clientWallAtRecvMs: osWallMs,
    });

    const deadlineMs = serverStartMs + 6 * 60 * 1000; // 6 minutes remaining
    const onFive = vi.fn();
    const onOne = vi.fn();
    const onExpire = vi.fn();

    const countdown = new ExamCountdown({
      deadlineAt: deadlineMs,
      clock,
      onFiveMinuteWarning: onFive,
      onOneMinuteWarning: onOne,
      onExpire,
    });

    expect(countdown.start().remainingMs).toBe(6 * 60 * 1000);

    // Student changes OS wall clock mid-exam (+4 hours) AND tab sleeps for 3 minutes (180_000ms)
    osWallMs += 4 * 60 * 60 * 1000;
    monoMs += 3 * 60 * 1000;
    vi.advanceTimersByTime(1_000); // Browser fires next throttled tick upon wake

    const afterSleep = countdown.snapshot();
    expect(afterSleep.remainingMs).toBe(3 * 60 * 1000);
    expect(onFive).toHaveBeenCalledTimes(1);
    expect(onOne).not.toHaveBeenCalled();

    // Sleep another 2m 15s (leaving 45s) -> hits one-minute warning once
    monoMs += 135_000;
    vi.advanceTimersByTime(1_000);
    expect(countdown.snapshot().remainingMs).toBe(45_000);
    expect(countdown.snapshot().phase).toBe("one_minute_warning");
    expect(onFive).toHaveBeenCalledTimes(1);
    expect(onOne).toHaveBeenCalledTimes(1);
    expect(onExpire).not.toHaveBeenCalled();

    // Sleep past deadline (1 minute) -> expires cleanly once
    monoMs += ONE_MINUTE_MS;
    vi.advanceTimersByTime(1_000);
    expect(countdown.snapshot().remainingMs).toBe(0);
    expect(onExpire).toHaveBeenCalledTimes(1);
  });
});
