import "fake-indexeddb/auto";

import { afterEach, describe, expect, it, vi } from "vitest";

import { computePayloadHash } from "../canonical-json.js";
import * as autosaveModule from "../exam-autosave-store.js";
import { ExamAutosaveStore, getDefaultExamAutosaveStore } from "../exam-autosave-store.js";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let dbCounter = 0;
function uniqueDbName(): string {
  dbCounter += 1;
  return `ulp_exam_test_${Date.now()}_${dbCounter}`;
}

describe("ExamAutosaveStore (Dexie + fake-indexeddb)", () => {
  const storesToClose: ExamAutosaveStore[] = [];

  afterEach(async () => {
    while (storesToClose.length > 0) {
      const store = storesToClose.pop();
      if (store) {
        await store.close();
      }
    }
    vi.restoreAllMocks();
  });

  it("persists to IndexedDB BEFORE making network request and sends full contract fields", async () => {
    const dbName = uniqueDbName();
    let seenInDbBeforeNetwork = false;
    const capturedBodies: Array<Record<string, unknown>> = [];
    const capturedHeaders: Array<string | null> = [];

    const store = new ExamAutosaveStore({
      dbName,
      apiBase: "https://exam.local",
      getToken: () => "jwt-token",
      fetchFn: async (_input, init) => {
        const rows = await store.getPendingRows("att-1");
        seenInDbBeforeNetwork = rows.length === 1;
        capturedBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        capturedHeaders.push((init?.headers as Record<string, string>)["Idempotency-Key"] ?? null);
        return jsonResponse(200, {
          status: "COMMITTED",
          committed_sequence: 1,
          saved_response_version: 1,
        });
      },
    });
    storesToClose.push(store);

    const payload = { selected_key: "B" };
    await store.enqueue("att-1", "q-1", payload, { flagged: true, autoFlush: false });
    await store.flush();

    expect(seenInDbBeforeNetwork).toBe(true);
    expect(capturedBodies).toHaveLength(1);
    const body = capturedBodies[0]!;
    expect(body.client_sequence).toBe(1);
    expect(body.expected_response_version).toBe(0);
    expect(body.idempotency_key).toBe(capturedHeaders[0]);
    expect(body.payload_hash).toBe(await computePayloadHash(payload));
    expect(body.response_payload).toEqual(payload);
    expect(body.is_flagged).toBe(true);
    expect(await store.getPendingRows("att-1")).toHaveLength(0);
    expect(await store.getExpectedVersion("att-1", "q-1")).toBe(1);
  });

  it("coalesces only unsent drafts (tries === 0) and preserves in-flight rows (tries > 0)", async () => {
    const dbName = uniqueDbName();
    let failNetwork = true;
    const sentPayloads: unknown[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      scheduleTimerFn: () => 0,
      fetchFn: async (_input, init) => {
        if (failNetwork) {
          throw new Error("offline");
        }
        const parsed = JSON.parse(String(init?.body)) as { response_payload: unknown };
        sentPayloads.push(parsed.response_payload);
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: sentPayloads.length });
      },
    });
    storesToClose.push(store);

    // Enqueue draft 1 and fail once -> tries becomes 1.
    await store.enqueue("att-1", "q-1", { selected_key: "A" }, { autoFlush: false });
    await store.flush();
    let pending = await store.getPendingRows("att-1");
    expect(pending).toHaveLength(1);
    expect(pending[0]?.tries).toBe(1);

    // Enqueue draft 2 (tries === 0) -> draft 1 (tries === 1) MUST be kept!
    await store.enqueue("att-1", "q-1", { selected_key: "B" }, { autoFlush: false });
    // Enqueue draft 3 -> draft 2 (tries === 0) is replaced, draft 1 (tries === 1) still preserved!
    await store.enqueue("att-1", "q-1", { selected_key: "C" }, { autoFlush: false });

    pending = await store.getPendingRows("att-1");
    expect(pending).toHaveLength(2);
    expect(pending[0]?.responsePayload).toEqual({ selected_key: "A" });
    expect(pending[0]?.tries).toBe(1);
    expect(pending[1]?.responsePayload).toEqual({ selected_key: "C" });
    expect(pending[1]?.tries).toBe(0);

    // Network recovers: both flush in order.
    failNetwork = false;
    await store.flush();
    expect(sentPayloads).toEqual([{ selected_key: "A" }, { selected_key: "C" }]);
    expect(await store.getPendingRows("att-1")).toHaveLength(0);
  });

  it("drains offline queue in FIFO order when network returns and handles online/visibilitychange", async () => {
    const dbName = uniqueDbName();
    let online = false;
    const deliveredQuestions: string[] = [];
    const scheduledDelays: number[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      baseBackoffMs: 500,
      jitterMaxMs: 250,
      randomFn: () => 0.5,
      scheduleTimerFn: (_cb, delay) => {
        scheduledDelays.push(delay);
        return 0;
      },
      fetchFn: async (input) => {
        if (!online) {
          throw new Error("ERR_INTERNET_DISCONNECTED");
        }
        const url = String(input);
        const qId = url.split("/").pop() ?? "";
        deliveredQuestions.push(qId);
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-2", "q-1", { selected_key: "A" }, { autoFlush: false });
    await store.enqueue("att-2", "q-2", { selected_key: "C" }, { autoFlush: false });
    await store.enqueue("att-2", "q-3", { text_value: "12" }, { autoFlush: false });

    // Offline attempt fails on q-1, increments tries and schedules backoff with jitter.
    await store.flush();
    expect(deliveredQuestions).toEqual([]);
    expect(scheduledDelays).toEqual([625]);

    // Network comes back: FIFO flush delivers q-1, q-2, q-3.
    online = true;
    await store.flush();
    expect(deliveredQuestions).toEqual(["q-1", "q-2", "q-3"]);
    expect(await store.getPendingRows("att-2")).toHaveLength(0);
  });

  it("survives simulated page reload (new Dexie instance reads persisted queue)", async () => {
    const dbName = uniqueDbName();
    const storeBeforeReload = new ExamAutosaveStore({
      dbName,
      scheduleTimerFn: () => 0,
      fetchFn: async () => {
        throw new Error("offline");
      },
    });
    await storeBeforeReload.enqueue("att-reload", "q-10", { selected_key: "D" }, { autoFlush: false });
    await storeBeforeReload.flush();
    await storeBeforeReload.close();

    // Simulate page reload with a fresh ExamAutosaveStore instance on the same IndexedDB.
    const flushedKeys: string[] = [];
    const storeAfterReload = new ExamAutosaveStore({
      dbName,
      fetchFn: async (_input, init) => {
        const body = JSON.parse(String(init?.body)) as { idempotency_key: string };
        flushedKeys.push(body.idempotency_key);
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(storeAfterReload);

    const pendingAfterReload = await storeAfterReload.getPendingRows("att-reload");
    expect(pendingAfterReload).toHaveLength(1);
    expect(pendingAfterReload[0]?.responsePayload).toEqual({ selected_key: "D" });

    await storeAfterReload.flush();
    expect(flushedKeys).toHaveLength(1);
    expect(await storeAfterReload.getPendingRows("att-reload")).toHaveLength(0);
    expect(await storeAfterReload.getExpectedVersion("att-reload", "q-10")).toBe(1);
  });

  it("deduplicates duplicate enqueue with same idempotencyKey and treats IDEMPOTENT_REPLAY as success", async () => {
    const dbName = uniqueDbName();
    let putCalls = 0;

    const store = new ExamAutosaveStore({
      dbName,
      fetchFn: async () => {
        putCalls += 1;
        return jsonResponse(200, {
          status: "IDEMPOTENT_REPLAY",
          committed_sequence: 1,
          saved_response_version: 1,
        });
      },
    });
    storesToClose.push(store);

    const fixedKey = "att-dup:q-1:seq_1:fixed-uuid";
    await store.enqueue("att-dup", "q-1", { selected_key: "A" }, {
      idempotencyKey: fixedKey,
      autoFlush: false,
    });
    await store.enqueue("att-dup", "q-1", { selected_key: "A" }, {
      idempotencyKey: fixedKey,
      autoFlush: false,
    });

    expect(await store.getPendingRows("att-dup")).toHaveLength(1);
    await store.flush();
    expect(putCalls).toBe(1);
    expect(await store.getPendingRows("att-dup")).toHaveLength(0);
    expect(await store.getExpectedVersion("att-dup", "q-1")).toBe(1);
  });

  it("resyncs on 409 version mismatch via GET /sync and then retries with synced version", async () => {
    const dbName = uniqueDbName();
    const sentExpectedVersions: number[] = [];
    const conflictCalls: Array<{ qId: string; serverPayload: unknown }> = [];
    let putCount = 0;

    const store = new ExamAutosaveStore({
      dbName,
      onConflictModal: (qId, serverPayload) => {
        conflictCalls.push({ qId, serverPayload });
      },
      fetchFn: async (input, init) => {
        const url = String(input);
        if (url.endsWith("/sync")) {
          return jsonResponse(200, {
            responses: {
              "q-9": {
                revision_no: 2,
                current_sequence: 2,
                last_idempotency_key: "other-session-key",
                response_payload: { selected_key: "A" },
              },
            },
          });
        }
        putCount += 1;
        const body = JSON.parse(String(init?.body)) as { expected_response_version: number };
        sentExpectedVersions.push(body.expected_response_version);
        if (putCount === 1) {
          return jsonResponse(409, { error_code: "OPTIMISTIC_VERSION_CONFLICT" });
        }
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 3 });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-sync", "q-9", { selected_key: "C" }, { autoFlush: false });
    await store.flush();

    expect(sentExpectedVersions).toEqual([0, 2]);
    expect(conflictCalls).toEqual([{ qId: "q-9", serverPayload: { selected_key: "A" } }]);
    expect(await store.getExpectedVersion("att-sync", "q-9")).toBe(3);
    expect(await store.getPendingRows("att-sync")).toHaveLength(0);
  });

  it("wires window online and document visibilitychange listeners cleanly", async () => {
    const dbName = uniqueDbName();
    const listeners = new Map<string, () => void>();
    const fakeWindow = {
      addEventListener: (ev: string, cb: () => void) => listeners.set(`win:${ev}`, cb),
      removeEventListener: (ev: string) => listeners.delete(`win:${ev}`),
    };
    const fakeDocument = {
      hidden: false,
      addEventListener: (ev: string, cb: () => void) => listeners.set(`doc:${ev}`, cb),
      removeEventListener: (ev: string) => listeners.delete(`doc:${ev}`),
    };
    vi.stubGlobal("window", fakeWindow);
    vi.stubGlobal("document", fakeDocument);

    let online = false;
    let calls = 0;
    const store = new ExamAutosaveStore({
      dbName,
      autoAttachWindowListeners: true,
      scheduleTimerFn: () => 0,
      fetchFn: async () => {
        if (!online) throw new Error("offline");
        calls += 1;
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-ev", "q-1", { selected_key: "B" }, { autoFlush: false });
    await store.flush();
    expect(calls).toBe(0);

    online = true;
    listeners.get("win:online")?.();
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toBe(1);
    expect(await store.getPendingRows("att-ev")).toHaveLength(0);
  });

  it("persists authRequired on 401/403, suppresses repeated enqueue flushes, and resumes on resume()", async () => {
    const dbName = uniqueDbName();
    const authEvents: Array<{ status: 401 | 403; errorCode?: string }> = [];
    let mode: "401" | "403_rbac" | "200" = "401";
    let fetchCalls = 0;

    const store = new ExamAutosaveStore({
      dbName,
      onAuthRequired: (status, errorCode) => authEvents.push({ status, errorCode }),
      fetchFn: async () => {
        fetchCalls += 1;
        if (mode === "401") {
          return jsonResponse(401, { error_code: "TOKEN_EXPIRED" });
        }
        if (mode === "403_rbac") {
          return jsonResponse(403, { error_code: "FORBIDDEN" });
        }
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: fetchCalls });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-auth", "q-1", { selected_key: "A" }, { autoFlush: false });
    await store.enqueue("att-auth", "q-2", { selected_key: "B" }, { autoFlush: false });

    // 401 stops flush immediately, sets authRequired, and keeps both rows.
    await store.flush();
    expect(fetchCalls).toBe(1);
    expect(await store.getPendingRows("att-auth")).toHaveLength(2);
    expect(await store.getQueueState()).toEqual({
      pending: 2,
      conflict: 0,
      rejected: 0,
      authRequired: true,
    });
    expect(authEvents).toEqual([{ status: 401, errorCode: "TOKEN_EXPIRED" }]);

    // Subsequent enqueue and flush while authRequired stay silent (no fetch, no duplicate callback).
    await store.enqueue("att-auth", "q-3", { selected_key: "C" });
    await store.flush();
    await new Promise((r) => setTimeout(r, 15));
    expect(fetchCalls).toBe(1);
    expect(authEvents).toHaveLength(1);
    expect((await store.getQueueState()).pending).toBe(3);

    // Calling resume() retries; a non-deadline 403 pauses again with updated callback.
    mode = "403_rbac";
    await store.resume();
    expect(fetchCalls).toBe(2);
    expect((await store.getQueueState()).authRequired).toBe(true);
    expect(authEvents).toEqual([
      { status: 401, errorCode: "TOKEN_EXPIRED" },
      { status: 403, errorCode: "FORBIDDEN" },
    ]);

    // After re-login + resume(), all kept rows flush cleanly.
    mode = "200";
    await store.resume();
    expect(await store.getPendingRows("att-auth")).toHaveLength(0);
    expect(await store.getQueueState()).toEqual({
      pending: 0,
      conflict: 0,
      rejected: 0,
      authRequired: false,
    });
  });

  it("deletes only matching attemptId on deadline-expired 403 and closed 409 without clearing other attempts", async () => {
    const dbName = uniqueDbName();
    const committed: string[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      fetchFn: async (input) => {
        const url = String(input);
        if (url.includes("/attempts/att-expired/")) {
          return jsonResponse(403, { error_code: "ATTEMPT_DEADLINE_EXPIRED" });
        }
        if (url.includes("/attempts/att-closed/")) {
          return jsonResponse(409, { error_code: "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED" });
        }
        committed.push(url);
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-expired", "q-1", { selected_key: "A" }, { autoFlush: false });
    await store.enqueue("att-expired", "q-2", { selected_key: "B" }, { autoFlush: false });
    await store.enqueue("att-closed", "q-1", { selected_key: "C" }, { autoFlush: false });
    await store.enqueue("att-active", "q-9", { selected_key: "D" }, { autoFlush: false });

    await store.flush();

    expect(await store.getPendingRows("att-expired")).toHaveLength(0);
    expect(await store.getPendingRows("att-closed")).toHaveLength(0);
    expect(await store.getPendingRows("att-active")).toHaveLength(0);
    expect(committed).toEqual(["/api/v1/attempts/att-active/responses/q-9"]);
  });

  it("marks row as conflict and calls onConflictModal on second 409 instead of deleting it", async () => {
    const dbName = uniqueDbName();
    const conflictCalls: Array<{ qId: string; serverPayload: unknown }> = [];
    const committed: string[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      onConflictModal: (qId, serverPayload) => conflictCalls.push({ qId, serverPayload }),
      fetchFn: async (input) => {
        const url = String(input);
        if (url.endsWith("/sync")) {
          return jsonResponse(200, {
            responses: {
              "q-conflict": {
                revision_no: 4,
                current_sequence: 4,
                last_idempotency_key: "other-device",
                response_payload: { selected_key: "B" },
              },
            },
          });
        }
        if (url.endsWith("/q-conflict")) {
          return jsonResponse(409, { error_code: "OPTIMISTIC_VERSION_CONFLICT" });
        }
        committed.push(url);
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-c2", "q-conflict", { selected_key: "A" }, { autoFlush: false });
    await store.enqueue("att-c2", "q-ok", { selected_key: "C" }, { autoFlush: false });

    await store.flush();

    const remaining = await store.getPendingRows("att-c2");
    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.questionVersionId).toBe("q-conflict");
    expect(remaining[0]?.status).toBe("conflict");
    expect(remaining[0]?.responsePayload).toEqual({ selected_key: "A" });
    expect(conflictCalls).toEqual([
      { qId: "q-conflict", serverPayload: { selected_key: "B" } },
      { qId: "q-conflict", serverPayload: { selected_key: "B" } },
    ]);
    expect(committed).toEqual(["/api/v1/attempts/att-c2/responses/q-ok"]);

    // Subsequent flush skips rows already in conflict state cleanly.
    await store.flush();
    expect(await store.getPendingRows("att-c2")).toHaveLength(1);
  });

  it("exposes getDefaultExamAutosaveStore lazily instead of constructing at import time", async () => {
    expect("defaultExamAutosaveStore" in autosaveModule).toBe(false);
    const s1 = getDefaultExamAutosaveStore();
    const s2 = getDefaultExamAutosaveStore();
    expect(s1).toBe(s2);
    storesToClose.push(s1);
  });

  it("resumes automatically when getToken() returns a fresh token after auth failure", async () => {
    const dbName = uniqueDbName();
    let token = "expired-jwt";
    const authEvents: number[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      getToken: () => token,
      onAuthRequired: (status) => authEvents.push(status),
      fetchFn: async (_input, init) => {
        const auth = (init?.headers as Record<string, string>)?.Authorization;
        if (auth !== "Bearer fresh-jwt") {
          return jsonResponse(401, { error_code: "TOKEN_EXPIRED" });
        }
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(store);

    await store.enqueue("att-tok", "q-1", { selected_key: "A" }, { autoFlush: false });
    await store.flush();
    expect(authEvents).toEqual([401]);
    expect((await store.getQueueState()).authRequired).toBe(true);

    // Same token -> enqueue does not auto-flush or re-trigger onAuthRequired.
    await store.enqueue("att-tok", "q-2", { selected_key: "B" });
    await new Promise((r) => setTimeout(r, 15));
    expect(authEvents).toEqual([401]);
    expect((await store.getQueueState()).pending).toBe(2);

    // Token refreshed -> next flush/enqueue detects change and drains queue.
    token = "fresh-jwt";
    expect((await store.getQueueState()).authRequired).toBe(false);
    await store.flush();
    expect(await store.getQueueState()).toEqual({
      pending: 0,
      conflict: 0,
      rejected: 0,
      authRequired: false,
    });
  });

  it("resolves conflict rows via keep_mine and take_server with latest synced version", async () => {
    const dbName = uniqueDbName();
    let forceConflict = true;
    const sentExpectedVersions: number[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      fetchFn: async (input, init) => {
        const url = String(input);
        if (url.endsWith("/sync")) {
          return jsonResponse(200, {
            responses: {
              "q-mine": {
                revision_no: 4,
                current_sequence: 4,
                last_idempotency_key: "other-1",
                response_payload: { selected_key: "B" },
              },
              "q-srv": {
                revision_no: 7,
                current_sequence: 7,
                last_idempotency_key: "other-2",
                response_payload: { selected_key: "D" },
              },
            },
          });
        }
        const body = JSON.parse(String(init?.body)) as { expected_response_version: number };
        if (url.endsWith("/q-mine")) {
          sentExpectedVersions.push(body.expected_response_version);
          if (forceConflict) {
            return jsonResponse(409, {
              error_code: "OPTIMISTIC_VERSION_CONFLICT",
              current_response_version: 5,
              current_sequence: 5,
            });
          }
          return jsonResponse(200, { status: "COMMITTED", saved_response_version: 6 });
        }
        if (forceConflict) {
          return jsonResponse(409, { error_code: "OPTIMISTIC_VERSION_CONFLICT" });
        }
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 8 });
      },
    });
    storesToClose.push(store);

    const mineRow = await store.enqueue("att-res", "q-mine", { selected_key: "A" }, { autoFlush: false });
    const srvRow = await store.enqueue("att-res", "q-srv", { selected_key: "C" }, { autoFlush: false });
    await store.flush();

    expect(await store.getQueueState("att-res")).toEqual({
      pending: 0,
      conflict: 2,
      rejected: 0,
      authRequired: false,
    });

    await store.resolveConflict(srvRow.id!, "take_server");
    expect(await store.getQueueState("att-res")).toEqual({
      pending: 0,
      conflict: 1,
      rejected: 0,
      authRequired: false,
    });

    forceConflict = false;
    await store.resolveConflict(mineRow.id!, "keep_mine");
    expect(await store.getQueueState("att-res")).toEqual({
      pending: 1,
      conflict: 0,
      rejected: 0,
      authRequired: false,
    });

    await store.flush();
    // Third PUT uses latest version (5 from second 409 body) and succeeds.
    expect(sentExpectedVersions).toEqual([0, 4, 5]);
    expect(await store.getQueueState("att-res")).toEqual({
      pending: 0,
      conflict: 0,
      rejected: 0,
      authRequired: false,
    });
  });

  it("marks other 4xx responses as rejected without retrying or blocking queue, deleting only on discard()", async () => {
    const dbName = uniqueDbName();
    let putCalls = 0;
    const committed: string[] = [];

    const store = new ExamAutosaveStore({
      dbName,
      fetchFn: async (input) => {
        putCalls += 1;
        const url = String(input);
        if (url.endsWith("/q-422")) {
          return jsonResponse(422, { error_code: "PAYLOAD_HASH_MISMATCH" });
        }
        if (url.endsWith("/q-404")) {
          return jsonResponse(404, {});
        }
        committed.push(url);
        return jsonResponse(200, { status: "COMMITTED", saved_response_version: 1 });
      },
    });
    storesToClose.push(store);

    const r422 = await store.enqueue("att-rej", "q-422", { selected_key: "A" }, { autoFlush: false });
    const r404 = await store.enqueue("att-rej", "q-404", { selected_key: "B" }, { autoFlush: false });
    await store.enqueue("att-rej", "q-ok", { selected_key: "C" }, { autoFlush: false });

    await store.flush();
    expect(putCalls).toBe(3);
    expect(committed).toEqual(["/api/v1/attempts/att-rej/responses/q-ok"]);

    const rows = await store.getPendingRows("att-rej");
    expect(rows).toHaveLength(2);
    expect(rows.find((r) => r.id === r422.id)?.status).toBe("rejected");
    expect(rows.find((r) => r.id === r422.id)?.errorCode).toBe("PAYLOAD_HASH_MISMATCH");
    expect(rows.find((r) => r.id === r404.id)?.status).toBe("rejected");
    expect(rows.find((r) => r.id === r404.id)?.errorCode).toBe("HTTP_404");

    expect(await store.getQueueState("att-rej")).toEqual({
      pending: 0,
      conflict: 0,
      rejected: 2,
      authRequired: false,
    });

    // Subsequent flush does NOT retry rejected items.
    await store.flush();
    expect(putCalls).toBe(3);

    await store.discard(r422.id!);
    expect(await store.getQueueState("att-rej")).toEqual({
      pending: 0,
      conflict: 0,
      rejected: 1,
      authRequired: false,
    });
    await store.discard(r404.id!);
    expect(await store.getPendingRows("att-rej")).toHaveLength(0);
  });
});

