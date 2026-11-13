import "fake-indexeddb/auto";

import { afterEach, describe, expect, it, vi } from "vitest";

import { computePayloadHash } from "../canonical-json.js";
import { ExamAutosaveStore } from "../exam-autosave-store.js";

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
});

