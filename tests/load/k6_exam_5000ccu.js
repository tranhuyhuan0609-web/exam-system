// tests/load/k6_exam_5000ccu.js — Mỗi VU có attempt_id riêng, tính SHA-256 thật & có kịch bản Submit Burst (Sửa P0-7)
import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import crypto from "k6/crypto";

const sessions = new SharedArray("sessions", () => JSON.parse(open("./data/sessions.json")));
const BASE_URL = __ENV.API_BASE_URL;
if (!BASE_URL) {
  throw new Error("API_BASE_URL is required, e.g. k6 run -e API_BASE_URL=https://<target> k6_exam_5000ccu.js");
}

export const options = {
  scenarios: {
    exam_5000_ccu_autosave: {
      executor: "ramping-vus",
      exec: "runAutosaveLoop",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 5000 },  // Pha 1: Synchronized Start Ramp-up
        { duration: "10m", target: 5000 }, // Pha 2: Steady-State Autosave (~333 - 500 RPS)
        { duration: "30s", target: 0 },
      ],
    },
    end_of_exam_submit_burst: {
      executor: "per-vu-iterations",
      exec: "runSubmitBurst",
      vus: 3000,
      iterations: 1,
      startTime: "11m",                    // Pha 3: 3.000 thí sinh bấm Nộp bài đồng loạt ở phút cuối
      maxDuration: "1m",
    },
  },
  thresholds: {
    "http_req_duration{operation:autosave}": ["p(50)<25", "p(95)<120", "p(99)<300"],
    "http_req_duration{operation:submit}": ["p(95)<250"],
    "http_req_failed": ["rate<0.0001"],
    "checks": ["rate>0.9999"],
  },
};

const vuState = {}; // Lưu { seq, version } theo từng câu hỏi của mỗi VU

export function runAutosaveLoop() {
  const s = sessions[(__VU - 1) % sessions.length];
  for (let i = 0; i < 40; i++) {
    const q = s.question_version_ids[i % s.question_version_ids.length];
    const st = vuState[q] || (vuState[q] = { seq: 0, version: 0 });
    st.seq += 1;

    const payload = { selected_key: ["A", "B", "C", "D"][st.seq % 4] };
    const idemKey = `${s.attempt_id}:${q}:seq_${st.seq}`;
    const payloadHash = crypto.sha256(JSON.stringify(payload), "hex");

    const body = JSON.stringify({
      client_sequence: st.seq,
      expected_response_version: st.version,
      idempotency_key: idemKey,
      payload_hash: payloadHash,
      response_payload: payload,
      is_flagged: false,
    });

    const params = {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${s.jwt}`,
        "Idempotency-Key": idemKey,
      },
      tags: { operation: "autosave" },
    };

    const url = `${BASE_URL}/api/v1/attempts/${s.attempt_id}/responses/${q}`;
    const res = http.put(url, body, params);
    const ok = check(res, { "autosave 200": (r) => r.status === 200 });
    if (ok) {
      st.version = res.json("saved_response_version");
    }

    // 5% xác suất gửi lặp cùng Idempotency-Key -> Phải nhận 200 IDEMPOTENT_REPLAY
    if (Math.random() < 0.05) {
      const rep = http.put(url, body, params);
      check(rep, {
        "replay 200": (r) => r.status === 200 && r.json("status") === "IDEMPOTENT_REPLAY",
      });
    }
    sleep(12 + Math.random() * 6);
  }
}

export function runSubmitBurst() {
  const s = sessions[(__VU - 1) % sessions.length];
  const submitUrl = `${BASE_URL}/api/v1/attempts/${s.attempt_id}/submit`;
  const res = http.post(submitUrl, JSON.stringify({ submit_idempotency_key: `sub:${s.attempt_id}` }), {
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${s.jwt}` },
    tags: { operation: "submit" },
  });
  check(res, { "submit 200": (r) => r.status === 200 });
}
