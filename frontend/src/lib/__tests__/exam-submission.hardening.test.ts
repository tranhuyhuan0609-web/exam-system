import { describe, expect, it, vi } from "vitest";
import {
  ExamSubmissionCoordinator,
  type AutosaveFlushOutcome,
  type SubmitAttemptRequest,
  type SubmitAttemptResponse,
} from "../exam-submission";

const okFlush = async (): Promise<AutosaveFlushOutcome> => ({
  flushedCount: 0,
  remainingPendingCount: 0,
  status: "ok",
});

describe("ExamSubmissionCoordinator hardening", () => {
  it("keeps input locked after expiry even when the flush is rejected, and across the retry", async () => {
    let flushStatus: AutosaveFlushOutcome["status"] = "rejected";
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: async () => ({ flushedCount: 0, remainingPendingCount: 1, status: flushStatus }),
      submitAttempt: async () => ({ status: "submitted" }),
    });
    const first = await coord.submit("auto_expire");
    expect(first.phase).toBe("rejected");
    expect(first.inputLocked).toBe(true);
    const retry = await coord.submit("retry");
    expect(retry.inputLocked).toBe(true);
    expect(retry.trigger).toBe("auto_expire");
    flushStatus = "auth_required";
    expect((await coord.submit("manual")).inputLocked).toBe(true);
  });

  it("keeps input locked after expiry when the server rejects the submit", async () => {
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: okFlush,
      submitAttempt: async () => ({ status: "rejected" }),
    });
    expect((await coord.submit("auto_expire")).inputLocked).toBe(true);
    expect((await coord.submit("retry")).inputLocked).toBe(true);
  });

  it("preserves the original trigger across offline retries (never sends 'retry')", async () => {
    let online = false;
    const triggers: string[] = [];
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: async () => ({
        flushedCount: 0,
        remainingPendingCount: online ? 0 : 3,
        status: online ? "ok" : "offline",
      }),
      submitAttempt: async (req) => {
        triggers.push(req.trigger);
        return { status: "submitted" };
      },
    });
    expect((await coord.submit("auto_expire")).trigger).toBe("auto_expire");
    expect((await coord.submit("retry")).trigger).toBe("auto_expire");
    online = true;
    await coord.onConnectionRestored();
    expect(triggers).toEqual(["auto_expire"]);

    const manual = new ExamSubmissionCoordinator({
      attemptId: "m",
      flushPendingAutosaves: async () => {
        throw new Error("down");
      },
      submitAttempt: async () => ({ status: "submitted" }),
    });
    await manual.submit("manual");
    expect((await manual.submit("retry")).trigger).toBe("manual");
  });

  it("upgrades a joined in-flight manual submit to auto_expire and locks input", async () => {
    let release!: (v: AutosaveFlushOutcome) => void;
    const submitAttempt = vi.fn(async (req: SubmitAttemptRequest): Promise<SubmitAttemptResponse> => {
      expect(req.trigger).toBe("auto_expire");
      return { status: "retryable_error" };
    });
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: () => new Promise((r) => (release = r)),
      submitAttempt,
    });
    const p1 = coord.submit("manual");
    const p2 = coord.submit("auto_expire");
    release({ flushedCount: 0, remainingPendingCount: 0, status: "ok" });
    const [r1, r2] = await Promise.all([p1, p2]);
    expect(submitAttempt).toHaveBeenCalledTimes(1);
    expect(r1).toEqual(r2);
    expect(r1.trigger).toBe("auto_expire");
    expect(r1.inputLocked).toBe(true);
  });

  it("reuses one key for all retries of a submission and issues a fresh one for a new submission", async () => {
    const keys: string[] = [];
    let n = 0;
    let mode: SubmitAttemptResponse["status"] = "retryable_error";
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      createIdempotencyKey: () => `k${(n += 1)}`,
      flushPendingAutosaves: okFlush,
      submitAttempt: async (req) => {
        keys.push(req.submitIdempotencyKey);
        return { status: mode };
      },
    });
    await coord.submit("manual");
    await coord.submit("retry");
    await coord.submit("retry");
    expect(keys).toEqual(["k1", "k1", "k1"]);
    mode = "sequence_conflict";
    await coord.submit("retry");
    expect(keys.at(-1)).toBe("k1");
    expect(coord.getSnapshot().phase).toBe("sequence_conflict");
    mode = "rejected";
    const next = await coord.submit("manual");
    expect(keys.at(-1)).toBe("k2");
    expect(next.retryCount).toBe(0);
    await coord.submit("manual");
    expect(keys.at(-1)).toBe("k3");
  });

  it("never reuses an old key even when the key factory is constant", async () => {
    const keys: string[] = [];
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      createIdempotencyKey: () => "same",
      flushPendingAutosaves: okFlush,
      submitAttempt: async (req) => {
        keys.push(req.submitIdempotencyKey);
        return { status: "rejected" };
      },
    });
    await coord.submit("manual");
    await coord.submit("manual");
    expect(new Set(keys).size).toBe(2);
  });

  it("uses exactly one network request for concurrent calls and none after submitted", async () => {
    const submitAttempt = vi.fn(async (): Promise<SubmitAttemptResponse> => ({ status: "submitted" }));
    const flush = vi.fn(okFlush);
    const coord = new ExamSubmissionCoordinator({ attemptId: "a", flushPendingAutosaves: flush, submitAttempt });
    const results = await Promise.all([
      coord.submit("manual"),
      coord.submit("auto_expire"),
      coord.submit("retry"),
      coord.onConnectionRestored(),
    ]);
    expect(submitAttempt).toHaveBeenCalledTimes(1);
    expect(flush).toHaveBeenCalledTimes(1);
    expect(results[0]?.phase).toBe("submitted");
    await coord.submit("manual");
    await coord.submitWithUnsyncedAnswers();
    expect(submitAttempt).toHaveBeenCalledTimes(1);
  });

  it("fails closed on NaN / Infinity / negative / unknown flush results", async () => {
    for (const remaining of [Number.NaN, Number.POSITIVE_INFINITY, -1]) {
      const submitAttempt = vi.fn(async (): Promise<SubmitAttemptResponse> => ({ status: "submitted" }));
      const coord = new ExamSubmissionCoordinator({
        attemptId: "a",
        flushPendingAutosaves: async () => ({ flushedCount: 0, remainingPendingCount: remaining, status: "ok" }),
        submitAttempt,
      });
      const snap = await coord.submit("manual");
      expect(snap.phase).toBe("offline_retry_banner");
      expect(Number.isFinite(snap.pendingCount)).toBe(true);
      expect(submitAttempt).not.toHaveBeenCalled();
    }
    const submitAttempt = vi.fn(async (): Promise<SubmitAttemptResponse> => ({ status: "submitted" }));
    const unknown = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: async () =>
        ({ flushedCount: 0, remainingPendingCount: 0, status: "weird" }) as unknown as AutosaveFlushOutcome,
      submitAttempt,
    });
    expect((await unknown.submit("manual")).phase).toBe("offline_retry_banner");
    expect(submitAttempt).not.toHaveBeenCalled();
  });

  it("falls back to a 2s backoff for invalid delays and reuses the last delay beyond the table", async () => {
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      retryDelaysMs: [Number.NaN, 500],
      flushPendingAutosaves: async () => {
        throw new Error("x");
      },
      submitAttempt: async () => ({ status: "submitted" }),
    });
    expect((await coord.submit("manual")).nextRetryDelayMs).toBe(2_000);
    expect((await coord.submit("retry")).nextRetryDelayMs).toBe(500);
    expect((await coord.submit("retry")).nextRetryDelayMs).toBe(500);
    const empty = new ExamSubmissionCoordinator({
      attemptId: "a",
      retryDelaysMs: [],
      flushPendingAutosaves: async () => {
        throw "str";
      },
      submitAttempt: async () => ({ status: "submitted" }),
    });
    const snap = await empty.submit("manual");
    expect(snap.nextRetryDelayMs).toBe(2_000);
    expect(snap.errorMessage).toBe("Network offline during autosave flush");
  });

  it("offers an explicit way out of a permanently rejected autosave batch without dropping answers", async () => {
    const requests: SubmitAttemptRequest[] = [];
    const flush = vi.fn(
      async (): Promise<AutosaveFlushOutcome> => ({
        flushedCount: 0,
        remainingPendingCount: 2,
        status: "rejected",
        unsyncedAnswerIds: ["q1", "q7"],
      }),
    );
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      createIdempotencyKey: () => "key-x",
      flushPendingAutosaves: flush,
      submitAttempt: async (req) => {
        requests.push(req);
        return { status: "submitted", receiptId: "r" };
      },
    });
    const blocked = await coord.submit("manual");
    expect(blocked.phase).toBe("rejected");
    expect(blocked.errorCode).toBe("AUTOSAVE_REJECTED");
    expect(blocked.canSubmitWithUnsynced).toBe(true);
    expect(blocked.unsyncedAnswerIds).toEqual(["q1", "q7"]);
    expect(requests).toHaveLength(0);

    const [a, b] = await Promise.all([coord.submitWithUnsyncedAnswers(), coord.submitWithUnsyncedAnswers()]);
    expect(a.phase).toBe("submitted");
    expect(b.phase).toBe("submitted");
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      submitIdempotencyKey: "key-x",
      trigger: "manual",
      forcedWithUnsyncedAnswers: true,
      unsyncedAnswerIds: ["q1", "q7"],
    });
    expect(flush).toHaveBeenCalledTimes(1);
  });

  it("rejects submitWithUnsyncedAnswers outside the rejected-autosave state", async () => {
    const idle = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: okFlush,
      submitAttempt: async () => ({ status: "submitted" }),
    });
    await expect(idle.submitWithUnsyncedAnswers()).rejects.toThrow(/permanently rejected/);

    const auth = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: async () => ({ flushedCount: 0, remainingPendingCount: 1, status: "auth_required" }),
      submitAttempt: async () => ({ status: "submitted" }),
    });
    expect((await auth.submit("manual")).canSubmitWithUnsynced).toBe(false);
    await expect(auth.submitWithUnsyncedAnswers()).rejects.toThrow();

    const offline = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: async () => ({ flushedCount: 0, remainingPendingCount: 1, status: "offline" }),
      submitAttempt: async () => ({ status: "submitted" }),
    });
    await offline.submit("manual");
    await expect(offline.submitWithUnsyncedAnswers()).rejects.toThrow();
  });

  it("forced submit after expiry stays locked and uses the original trigger", async () => {
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: async () => ({
        flushedCount: 0,
        remainingPendingCount: 1,
        status: "rejected",
        unsyncedAnswerIds: ["q2"],
      }),
      submitAttempt: async (req) => {
        expect(req.trigger).toBe("auto_expire");
        return { status: "retryable_error" };
      },
    });
    await coord.submit("auto_expire");
    const after = await coord.submitWithUnsyncedAnswers("manual");
    expect(after.inputLocked).toBe(true);
    expect(after.phase).toBe("offline_retry_banner");
    expect(after.canSubmitWithUnsynced).toBe(false);
  });

  it("rejects an empty attemptId and returns snapshot copies", () => {
    expect(
      () =>
        new ExamSubmissionCoordinator({
          attemptId: "",
          flushPendingAutosaves: okFlush,
          submitAttempt: async () => ({ status: "submitted" }),
        }),
    ).toThrow(TypeError);
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: okFlush,
      submitAttempt: async () => ({ status: "submitted" }),
    });
    const snap = coord.getSnapshot();
    (snap as { phase: string }).phase = "submitted";
    expect(coord.getSnapshot().phase).toBe("idle");
  });

  it("emits transitions to onStateChange; onConnectionRestored is a no-op outside the banner", async () => {
    const phases: string[] = [];
    const coord = new ExamSubmissionCoordinator({
      attemptId: "a",
      flushPendingAutosaves: okFlush,
      submitAttempt: async () => ({ status: "already_submitted" }),
      onStateChange: (s) => phases.push(s.phase),
    });
    expect((await coord.onConnectionRestored()).phase).toBe("idle");
    expect(phases).toEqual([]);
    await coord.submit("manual");
    expect(phases).toEqual(["flushing_autosave", "submitting", "submitted"]);
    expect(coord.getSnapshot().receiptId).toBe("submit:a:1");
  });
});
