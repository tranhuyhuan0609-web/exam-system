/**
 * Offline-first exam autosave store backed by real IndexedDB via Dexie (H-04, C06).
 *
 * Guarantees:
 * 1. Every answer change is durably persisted to IndexedDB BEFORE any network request.
 * 2. Coalescing only deletes unsent drafts (`tries === 0`), preserving in-flight/retried rows (`tries > 0`).
 * 3. Every request carries `client_sequence`, `expected_response_version`, `idempotency_key`,
 *    and `payload_hash` matching the Python Canonical Autosave Contract.
 * 4. `IDEMPOTENT_REPLAY` (HTTP 200) is treated identically to `COMMITTED`.
 * 5. `409` triggers `/sync` resync before continuing; `429`/`5xx`/network errors back off with jitter.
 */

import Dexie, { type Table } from "dexie";

import { computePayloadHash } from "./canonical-json.js";

export type ExamPayloadScalar = string | number | boolean | null;
export type ExamPayloadValue =
  | ExamPayloadScalar
  | ExamPayloadValue[]
  | { [key: string]: ExamPayloadValue };
export type ExamPayload = Record<string, ExamPayloadValue>;

export interface PendingAutosave {
  id?: number;
  attemptId: string;
  questionVersionId: string;
  clientSequence: number;
  idempotencyKey: string;
  payloadHash: string;
  responsePayload: ExamPayload;
  isFlagged: boolean;
  createdAtMs: number;
  tries: number;
}

export interface MetaRow {
  key: string;
  value: number;
}

export interface AutosaveSuccessBody {
  status: "COMMITTED" | "IDEMPOTENT_REPLAY";
  committed_sequence?: number;
  saved_response_version: number;
  server_timestamp?: string;
}

export interface SyncResponseItem {
  revision_no: number;
  current_sequence: number;
  last_idempotency_key: string | null;
  response_payload: unknown;
}

export interface SyncResponseBody {
  responses: Record<string, SyncResponseItem>;
}

export interface ExamAutosaveStoreOptions {
  dbName?: string;
  apiBase?: string;
  getToken?: () => string;
  fetchFn?: typeof fetch;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  jitterMaxMs?: number;
  randomFn?: () => number;
  nowMsFn?: () => number;
  uuidFn?: () => string;
  scheduleTimerFn?: (cb: () => void, delayMs: number) => unknown;
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void;
  autoAttachWindowListeners?: boolean;
}

export class ExamDatabase extends Dexie {
  pending!: Table<PendingAutosave, number>;
  meta!: Table<MetaRow, string>;

  constructor(dbName = "ulp_exam_offline_db") {
    super(dbName);
    this.version(1).stores({
      pending: "++id, attemptId, [attemptId+questionVersionId], idempotencyKey",
      meta: "key",
    });
  }
}

const seqKey = (attemptId: string, questionVersionId: string): string =>
  `seq:${attemptId}:${questionVersionId}`;

const verKey = (attemptId: string, questionVersionId: string): string =>
  `ver:${attemptId}:${questionVersionId}`;

export class ExamAutosaveStore {
  readonly db: ExamDatabase;
  private readonly apiBase: string;
  private readonly getToken: () => string;
  private readonly fetchFn: typeof fetch;
  private readonly baseBackoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly jitterMaxMs: number;
  private readonly randomFn: () => number;
  private readonly nowMsFn: () => number;
  private readonly uuidFn: () => string;
  private readonly scheduleTimerFn: (cb: () => void, delayMs: number) => unknown;
  private readonly onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void;

  private flushing = false;
  private activeFlushPromise: Promise<void> | null = null;
  private detachListeners: (() => void) | null = null;

  constructor(options: ExamAutosaveStoreOptions = {}) {
    this.db = new ExamDatabase(options.dbName ?? "ulp_exam_offline_db");
    this.apiBase = options.apiBase ?? "";
    this.getToken = options.getToken ?? (() => "");
    this.fetchFn = options.fetchFn ?? ((...args) => globalThis.fetch(...args));
    this.baseBackoffMs = options.baseBackoffMs ?? 500;
    this.maxBackoffMs = options.maxBackoffMs ?? 30_000;
    this.jitterMaxMs = options.jitterMaxMs ?? 500;
    this.randomFn = options.randomFn ?? Math.random;
    this.nowMsFn = options.nowMsFn ?? Date.now;
    this.uuidFn = options.uuidFn ?? (() => globalThis.crypto.randomUUID());
    this.scheduleTimerFn =
      options.scheduleTimerFn ?? ((cb, delayMs) => globalThis.setTimeout(cb, delayMs));
    this.onConflictModal = options.onConflictModal;

    if (options.autoAttachWindowListeners) {
      this.attachWindowListeners();
    }
  }

  attachWindowListeners(): () => void {
    if (this.detachListeners) {
      return this.detachListeners;
    }
    const onOnline = () => {
      void this.flush();
    };
    const onVisibilityChange = () => {
      if (typeof document !== "undefined" && !document.hidden) {
        void this.flush();
      }
    };
    if (typeof window !== "undefined") {
      window.addEventListener("online", onOnline);
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    this.detachListeners = () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("online", onOnline);
      }
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      this.detachListeners = null;
    };
    return this.detachListeners;
  }

  async close(): Promise<void> {
    if (this.detachListeners) {
      this.detachListeners();
    }
    await this.activeFlushPromise?.catch(() => undefined);
    this.db.close();
  }

  async getExpectedVersion(attemptId: string, questionVersionId: string): Promise<number> {
    return (await this.db.meta.get(verKey(attemptId, questionVersionId)))?.value ?? 0;
  }

  async getPendingRows(attemptId?: string): Promise<PendingAutosave[]> {
    if (attemptId !== undefined) {
      return this.db.pending.where("attemptId").equals(attemptId).sortBy("id");
    }
    return this.db.pending.orderBy("id").toArray();
  }

  /**
   * Persist an autosave change to IndexedDB BEFORE triggering network flush.
   * Passing an existing `explicitIdempotencyKey` prevents duplicate pending entries.
   */
  async enqueue(
    attemptId: string,
    questionVersionId: string,
    payload: ExamPayload,
    options: {
      flagged?: boolean;
      idempotencyKey?: string;
      autoFlush?: boolean;
    } = {},
  ): Promise<PendingAutosave> {
    const flagged = options.flagged ?? false;
    const payloadHash = await computePayloadHash(payload);

    const saved = await this.db.transaction("rw", this.db.pending, this.db.meta, async () => {
      if (options.idempotencyKey) {
        const duplicate = await this.db.pending
          .where("idempotencyKey")
          .equals(options.idempotencyKey)
          .first();
        if (duplicate) {
          return duplicate;
        }
      }

      const currentSeq = (await this.db.meta.get(seqKey(attemptId, questionVersionId)))?.value ?? 0;
      const seq = currentSeq + 1;
      await this.db.meta.put({ key: seqKey(attemptId, questionVersionId), value: seq });

      // Only coalesce drafts that have NEVER been sent (`tries === 0`).
      const existing = await this.db.pending
        .where("[attemptId+questionVersionId]")
        .equals([attemptId, questionVersionId])
        .toArray();
      const unsentIds = existing
        .filter((row) => row.tries === 0 && row.id !== undefined)
        .map((row) => row.id as number);
      if (unsentIds.length > 0) {
        await this.db.pending.bulkDelete(unsentIds);
      }

      const idempotencyKey =
        options.idempotencyKey ?? `${attemptId}:${questionVersionId}:seq_${seq}:${this.uuidFn()}`;
      const record: PendingAutosave = {
        attemptId,
        questionVersionId,
        clientSequence: seq,
        idempotencyKey,
        payloadHash,
        responsePayload: payload,
        isFlagged: flagged,
        createdAtMs: this.nowMsFn(),
        tries: 0,
      };
      const id = await this.db.pending.add(record);
      return { ...record, id };
    });

    if (options.autoFlush !== false) {
      void this.flush();
    }
    return saved;
  }

  async flush(): Promise<void> {
    if (this.flushing && this.activeFlushPromise) {
      return this.activeFlushPromise;
    }
    this.flushing = true;
    this.activeFlushPromise = this._runFlushLoop().finally(() => {
      this.flushing = false;
      this.activeFlushPromise = null;
    });
    return this.activeFlushPromise;
  }

  private async _runFlushLoop(): Promise<void> {
    const resyncedIds = new Set<number>();

    while (true) {
      const rows = await this.db.pending.orderBy("id").toArray();
      if (rows.length === 0) {
        return;
      }
      const r = rows[0];
      if (!r || r.id === undefined) {
        return;
      }

      const expected =
        (await this.db.meta.get(verKey(r.attemptId, r.questionVersionId)))?.value ?? 0;
      const token = this.getToken();
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "Idempotency-Key": r.idempotencyKey,
      };
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }

      let res: Response;
      try {
        res = await this.fetchFn(
          `${this.apiBase}/api/v1/attempts/${r.attemptId}/responses/${r.questionVersionId}`,
          {
            method: "PUT",
            headers,
            body: JSON.stringify({
              client_sequence: r.clientSequence,
              expected_response_version: expected,
              idempotency_key: r.idempotencyKey,
              payload_hash: r.payloadHash,
              response_payload: r.responsePayload,
              is_flagged: r.isFlagged,
            }),
          },
        );
      } catch {
        await this._scheduleBackoff(r);
        return;
      }

      if (res.status === 200) {
        const body = (await res.json()) as AutosaveSuccessBody;
        // Both COMMITTED and IDEMPOTENT_REPLAY are treated as successful persistence.
        await this.db.meta.put({
          key: verKey(r.attemptId, r.questionVersionId),
          value: body.saved_response_version,
        });
        await this.db.pending.delete(r.id);
        continue;
      }

      if (res.status >= 500 || res.status === 429) {
        await this._scheduleBackoff(r);
        return;
      }

      if (res.status === 403) {
        // Attempt deadline expired on server: discard remaining unsent drafts.
        await this.db.pending.clear();
        return;
      }

      if (res.status === 409) {
        let errorCode: string | undefined;
        try {
          const errBody = (await res.clone().json()) as { error_code?: string };
          errorCode = errBody.error_code;
        } catch {
          errorCode = undefined;
        }

        if (errorCode === "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED") {
          await this.db.pending.clear();
          return;
        }

        if (resyncedIds.has(r.id)) {
          await this.db.pending.delete(r.id);
          continue;
        }
        resyncedIds.add(r.id);

        const action = await this._resync(r);
        if (action === "retry_row") {
          continue;
        }
        if (action === "stop") {
          return;
        }
        // action === "resolved_deleted" -> continue with next queued row
        continue;
      }

      // Other 4xx (e.g. 422 PAYLOAD_HASH_MISMATCH): drop malformed entry and move on.
      await this.db.pending.delete(r.id);
    }
  }

  private async _scheduleBackoff(r: PendingAutosave): Promise<void> {
    if (r.id === undefined) return;
    await this.db.pending.update(r.id, { tries: r.tries + 1 });
    const expDelay = Math.min(this.maxBackoffMs, this.baseBackoffMs * 2 ** r.tries);
    const delay = expDelay + this.randomFn() * this.jitterMaxMs;
    this.scheduleTimerFn(() => {
      void this.flush();
    }, delay);
  }

  private async _resync(
    r: PendingAutosave,
  ): Promise<"resolved_deleted" | "retry_row" | "stop"> {
    if (r.id === undefined) return "stop";
    const token = this.getToken();
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    let syncRes: Response;
    try {
      syncRes = await this.fetchFn(`${this.apiBase}/api/v1/attempts/${r.attemptId}/sync`, {
        headers,
      });
    } catch {
      await this._scheduleBackoff(r);
      return "stop";
    }

    if (!syncRes.ok) {
      return "stop";
    }

    const syncData = (await syncRes.json()) as SyncResponseBody;
    const srv = syncData.responses[r.questionVersionId];
    if (!srv) {
      return "retry_row";
    }

    await this.db.meta.put({
      key: verKey(r.attemptId, r.questionVersionId),
      value: srv.revision_no,
    });
    const nextSeq = Math.max(r.clientSequence, srv.current_sequence);
    await this.db.meta.put({
      key: seqKey(r.attemptId, r.questionVersionId),
      value: nextSeq,
    });

    if (srv.last_idempotency_key === r.idempotencyKey) {
      await this.db.pending.delete(r.id);
      return "resolved_deleted";
    }

    if (this.onConflictModal) {
      this.onConflictModal(r.questionVersionId, srv.response_payload);
    }

    // Re-attempt sending this item with the newly synced expected_response_version
    // and, if sequence was stale, bumped client_sequence.
    if (r.clientSequence <= srv.current_sequence) {
      const bumpedSeq = srv.current_sequence + 1;
      await this.db.meta.put({
        key: seqKey(r.attemptId, r.questionVersionId),
        value: bumpedSeq,
      });
      await this.db.pending.update(r.id, {
        clientSequence: bumpedSeq,
        tries: r.tries + 1,
      });
    } else {
      await this.db.pending.update(r.id, { tries: r.tries + 1 });
    }
    return "retry_row";
  }
}

export function createExamAutosaveStore(options?: ExamAutosaveStoreOptions): ExamAutosaveStore {
  return new ExamAutosaveStore(options);
}

export const defaultExamAutosaveStore = new ExamAutosaveStore({
  autoAttachWindowListeners: true,
});

