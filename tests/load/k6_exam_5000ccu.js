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

// The gate runs the defaults (5,000 VUs, 3,000 submitting). A smaller server (the 4 vCPU pilot) is stepped up with
//   -e PEAK_VUS=200 -e SUBMIT_VUS=150 -e RAMP=1m -e STEADY=3m
// and the thresholds below stay the same: a size that passes them is the size the server holds.
const PEAK_VUS = Number(__ENV.PEAK_VUS || 5000);
const SUBMIT_VUS = Number(__ENV.SUBMIT_VUS || 3000);
const RAMP = __ENV.RAMP || "1m";
const STEADY = __ENV.STEADY || "10m";
const SUBMIT_AT = __ENV.SUBMIT_AT || "11m"; // after RAMP + STEADY + the 30 s ramp-down

export const options = {
  scenarios: {
    exam_5000_ccu_autosave: {
      executor: "ramping-vus",
      exec: "runAutosaveLoop",
      startVUs: 0,
      stages: [
        { duration: RAMP, target: PEAK_VUS },   // Pha 1: Synchronized Start Ramp-up
        { duration: STEADY, target: PEAK_VUS }, // Pha 2: Steady-State Autosave (~333 - 500 RPS at 5,000)
        { duration: "30s", target: 0 },
      ],
    },
    end_of_exam_submit_burst: {
      executor: "per-vu-iterations",
      exec: "runSubmitBurst",
      vus: SUBMIT_VUS,
      iterations: 1,
      startTime: SUBMIT_AT,                // Pha 3: thí sinh bấm Nộp bài đồng loạt ở phút cuối
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
  // The API reads the key from the Idempotency-Key header (POST /attempts/{id}/submit takes no body).
  const res = http.post(submitUrl, null, {
    headers: { Authorization: `Bearer ${s.jwt}`, "Idempotency-Key": `sub:${s.attempt_id}` },
    tags: { operation: "submit" },
  });
  check(res, { "submit 200": (r) => r.status === 200 });
}
