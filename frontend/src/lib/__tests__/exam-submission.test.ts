import { describe, expect, it, vi } from "vitest";
import {
  ExamSubmissionCoordinator,
  type AutosaveFlushOutcome,
  type SubmitAttemptRequest,
  type SubmitAttemptResponse,
} from "../exam-submission";

describe("ExamSubmissionCoordinator", () => {
  it("deduplicates concurrent manual + auto_expire submits and flushes autosaves first", async () => {
    const order: string[] = [];
    let resolveFlush!: (v: AutosaveFlushOutcome) => void;

    const flushPendingAutosaves = vi.fn(
      () =>
        new Promise<AutosaveFlushOutcome>((resolve) => {
          order.push("flush_start");
          resolveFlush = resolve;
        }),
    );

    const submitAttempt = vi.fn(async (req: SubmitAttemptRequest): Promise<SubmitAttemptResponse> => {
      order.push(`submit:${req.submitIdempotencyKey}`);
      return {
        status: "submitted",
        receiptId: "rcpt-100",
        submittedAtServerIso: "2026-11-13T11:30:00Z",
      };
    });

    const coordinator = new ExamSubmissionCoordinator({
      attemptId: "att-1",
      flushPendingAutosaves,
      submitAttempt,
      createIdempotencyKey: () => "stable-key-1",
    });

    const pManual = coordinator.submit("manual");
    const pExpire = coordinator.submit("auto_expire");
    expect(flushPendingAutosaves).toHaveBeenCalledTimes(1);

    resolveFlush({ flushedCount: 4, remainingPendingCount: 0, status: "ok" });
    const [resManual, resExpire] = await Promise.all([pManual, pExpire]);

    expect(order).toEqual(["flush_start", "submit:stable-key-1"]);
    expect(submitAttempt).toHaveBeenCalledTimes(1);
    expect(resManual.phase).toBe("submitted");
    expect(resExpire.phase).toBe("submitted");
    expect(resManual.receiptId).toBe("rcpt-100");
    expect(resManual.inputLocked).toBe(true);
  });

  it("locks input on auto_expire during outage, shows offline banner, and reuses exact idempotency key on recovery", async () => {
    let online = false;
    const seenKeys: string[] = [];

    const coordinator = new ExamSubmissionCoordinator({
      attemptId: "att-offline",
      retryDelaysMs: [1_000, 3_000],
      flushPendingAutosaves: async () =>
        online
          ? { flushedCount: 2, remainingPendingCount: 0, status: "ok" }
          : { flushedCount: 0, remainingPendingCount: 2, status: "offline" },
      submitAttempt: async (req) => {
        seenKeys.push(req.submitIdempotencyKey);
        return { status: "already_submitted", receiptId: "rcpt-offline-ok" };
      },
      createIdempotencyKey: () => "idem-offline-999",
    });

    // Time expires while student is offline: input MUST lock immediately and banner must appear
    const first = await coordinator.submit("auto_expire");
    expect(first.inputLocked).toBe(true);
    expect(first.phase).toBe("offline_retry_banner");
    expect(first.pendingCount).toBe(2);
    expect(first.retryCount).toBe(1);
    expect(first.nextRetryDelayMs).toBe(1_000);
    expect(seenKeys).toEqual([]);

    // Network drops again on manual retry -> second backoff step
    const second = await coordinator.submit("retry");
    expect(second.retryCount).toBe(2);
    expect(second.nextRetryDelayMs).toBe(3_000);

    // Network returns -> recovery completes with exact same idempotency key
    online = true;
    const recovered = await coordinator.onConnectionRestored();
    expect(recovered.phase).toBe("submitted");
    expect(recovered.receiptId).toBe("rcpt-offline-ok");
    expect(seenKeys).toEqual(["idem-offline-999"]);
  });

  it("surfaces sequence_conflict cleanly and keeps non-expire inputs unlocked on auth rejection", async () => {
    const conflictCoord = new ExamSubmissionCoordinator({
      attemptId: "att-seq",
      flushPendingAutosaves: async () => ({ flushedCount: 0, remainingPendingCount: 0, status: "ok" }),
      submitAttempt: async () => ({
        status: "sequence_conflict",
        errorCode: "STALE_CLIENT_SEQUENCE",
        errorMessage: "Opened in another tab with higher sequence",
      }),
    });

    const conflict = await conflictCoord.submit("manual");
    expect(conflict.phase).toBe("sequence_conflict");
    expect(conflict.errorCode).toBe("STALE_CLIENT_SEQUENCE");

    const authFailCoord = new ExamSubmissionCoordinator({
      attemptId: "att-auth",
      flushPendingAutosaves: async () => ({
        flushedCount: 0,
        remainingPendingCount: 1,
        status: "auth_required",
        errorMessage: "Session expired",
      }),
      submitAttempt: async () => ({ status: "submitted" }),
    });

    const authState = await authFailCoord.submit("manual");
    expect(authState.phase).toBe("rejected");
    expect(authState.errorCode).toBe("AUTH_REQUIRED");
    expect(authState.inputLocked).toBe(false);
  });
});
