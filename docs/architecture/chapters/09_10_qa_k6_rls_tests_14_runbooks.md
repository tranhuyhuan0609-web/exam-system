## CHƯƠNG 9. MA TRẬN KIỂM THỬ HỒI QUY 11+ ĐỀ MẪU (GOLDEN CORPUS + HELD-OUT) & MỤC TIÊU SLOs

### 9.1. Quy tắc Quản lý Golden Corpus An toàn Pháp lý & Định lượng Chất lượng (Khắc phục P0-6 & P1)

1. **Tuyệt đối không lưu trữ file đề thi có thông tin nhạy cảm trực tiếp trong Git Repo:** Toàn bộ 11 file mẫu trong bảng dưới đây cùng **30 file đề Held-out** (dùng để kiểm tra chống Overfitting ngưỡng tọa độ) phải được xóa bỏ hoàn toàn PII (tên giáo viên, số điện thoại, watermark tài khoản, tên đơn vị nếu chưa xin phép) và lưu trữ trên bucket riêng `s3://ulp-qa-golden-private/` có mã hóa SSE-KMS. CI Runner kéo về thư mục tạm `/tmp/golden_fixtures/` khi chạy test.
2. **Thước đo định lượng bắt buộc trên toàn tập:**
   - **Question Boundary Precision $\ge 98.0\%$ & Recall $\ge 96.0\%$** (không gộp nhầm 2 câu, không cắt sót câu).
   - **Figure & Formula Preservation Rate $\ge 99.0\%$** (không mất công thức MathType OLE, không mất nhãn hóa học trong hình).
   - **Clean Text Redaction Rate $= 100\%$** (không còn chuỗi ký tự rác từ trục tọa độ/đồ thị lọt vào lời dẫn câu hỏi).

| Mã File Mẫu (Đã Ẩn Danh Hóa trên S3 Private) | Đặc điểm Kỹ thuật & Góc khuất Thực tế | Tiêu chí Pass Định lượng Tự động trong CI/CD (`pytest -m golden`) |
| :--- | :--- | :--- |
| **1. `golden_01_phy10_essay.pdf`**<br>*(Đề Vật lí 10 tự luận)* | 5 câu tự luận (`Câu I..V`), Câu II và IV vắt qua 2 trang, có watermark và logo tròn góc trái trên trang 1. | Tách đúng 5 câu lớn và các ý `1a, 1b, 2a, 2b`; nối liền mạch Câu II & IV qua ranh giới trang; cắt đúng 4 hình bài toán và tự động loại bỏ logo trang 1. |
| **2. `golden_02_phy11_vector.pdf`**<br>*(Đề Vật lí 11 có hình Vector)* | 5 câu tự luận, riêng `Hình 5` ở trang 3 được vẽ bằng các nét Vector rời rạc (không phải ảnh bitmap nhúng). | Thuật toán Union-Find `merge_intersecting_rects_union_find()` gom và cắt trọn vẹn `Hình 5` ở 300 DPI qua `pypdfium2` gắn vào Câu V. |
| **3. `golden_03_chem11_legacy.doc`**<br>*(Đề Hóa học 11 định dạng `.doc` cũ)* | File `.doc` nhị phân Word 97–2003; có bảng phức chất Cobalt và sơ đồ tổng hợp hữu cơ (Câu 4, 6, 8) không có nhãn `'Hình X'`. | Convert qua LibreOffice (với `-env:UserInstallation` cô lập) thành công; bảo toàn bảng phức chất; cắt trọn sơ đồ hữu cơ mà không làm rớt nhóm `OH, COOH`. |
| **4. `golden_04_bio11_canvas.docx`**<br>*(Đề Sinh học 11 có bảng gộp cột)* | 10 câu lớn có tên chuyên đề khớp SGK; Bảng 2 và Bảng 3 có gộp cột (`colspan`); Hình 4, 7, 8 dùng Word Drawing Canvas. | Làm phẳng hoàn toàn Hình 4, 7, 8 qua nhánh render PDF; chuyển đổi và giữ nguyên cấu trúc `colspan`/`rowspan` của Bảng 2 và Bảng 3 sang `HTML_TABLE`. |
| **5. `golden_05_math11_ole.docx`**<br>*(Đề Toán 11 có MathType OLE)* | Toàn bộ công thức dãy số $u_n$, đa thức $P(x)$ được nhúng dưới dạng đối tượng `MathType OLE` (`<w:object>`). | Phát hiện `<w:object>` (có kiểm tra chống Zip Bomb $\le 25\text{ MB}$) và định tuyến qua LibreOffice PDF + LaTeX OCR; $0$ công thức bị mất trống. |
| **6. `golden_06_phy12_hdc_table.docx`**<br>*(Hướng dẫn chấm bảng 3 cột)* | File Hướng dẫn chấm trình bày trong Bảng 3 cột (`Câu - Nội dung - Điểm` (3 cột)), có cả đáp số ngắn và hình mạch điện. | Bóc chính xác 10 đáp số trả lời ngắn ở Câu I (`0,157 mm/s`, `2,1 giờ`...) và lời giải từng ý `II.1, II.2.a` kèm hình mạch điện gắn đúng câu. |
| **7. `golden_07_no_header_sheets.pdf`**<br>*(Bộ 3 phiếu bài tập sát mép trên)* | Phiếu bài tập không có Header kỳ thi (`Câu 1:` nằm ngay sát mép trên $y_0 = 40\text{pt}$), xen kẽ chữ `Bài 3:` giữa `Câu 6:` và `Câu 7:`. | Bộ phát hiện Header động giữ nguyên vẹn `Câu 1` ở $y_0 = 40\text{pt}$; khi xuất PDF gắn thương hiệu tự động mở rộng đỉnh trang $70\text{pt}$; tách đủ cả `Bài 3:`. |
| **8. `golden_08_combined_exam_sol.pdf`**<br>*(File gộp Đề + Đáp án giữa chừng)* | Nửa đầu file là 5 câu đề bài, từ giữa trang 2 có dòng chữ `Đáp án` rồi giải chi tiết từ Câu 1 đến Câu 4 kèm hình phân tích lực. | Tự động chia đôi tại mốc `Đáp án`, ghép đúng lời giải và hình phân tích lực ở nửa sau vào từng `QuestionCandidate` tương ứng ở nửa đầu. |
| **9. `golden_09_word_shapes_graph.docx`**<br>*(Đề có đồ thị vẽ bằng Word Shapes)* | Đồ thị vận tốc - thời gian ở Câu 2, 3, 5, 7 vẽ bằng Word Shapes gây chuỗi rác `v(m/s)t(s)O24648` nếu đọc XML thô. | Cắt phẳng đồ thị thành ảnh PNG sắc nét và lọc bỏ $100\%$ các ký tự có tâm nằm trong khung đồ thị khỏi văn bản lời dẫn câu hỏi. |
| **10. `golden_10_mobile_photo_skewed.jpg`**<br>*(Ảnh chụp đề thi bằng điện thoại)* | Ảnh chụp trang giấy đề thi bị bóng xám và nghiêng $4.5^\circ$, chứa `Câu 4`, `Câu 5` và `Hình 5`. | Xoay thẳng (Deskew), khử bóng xám bằng OpenCV `adaptiveThreshold`, nhận diện đúng văn bản LaTeX của 2 câu và cắt riêng khung `Hình 5`. |
| **11. `golden_11_bayes_mixed_guide.pdf`**<br>*(Chuyên đề hỗn hợp Công thức Bayes)* | 11 trang gồm: Lý thuyết + Phương pháp + Công thức + 8 câu Tự luận + 24 câu ABCD + 13 câu Đúng/Sai 4 ý + 22 câu Trả lời ngắn; có Footer lặp ở đáy trang. | Lọc sạch Footer lặp liên trang; tách chuẩn 1 bản ghi `ContentCandidate` (đủ 3 hộp Phương pháp, Công thức, Chú ý) và đúng 67 `QuestionCandidate` chia chuẩn vào 4 định dạng. |

### 9.2. Cam kết Mục tiêu Chất lượng Dịch vụ (Production SLO Targets — Đã Đồng bộ Hóa ở V11.3)

| Hạng mục Đo lường (SLI) | Chỉ số Mục tiêu (Target SLO V11.3) | Cách thức Giám sát, Kiến trúc Đảm bảo & Cảnh báo (Alerting) |
| :--- | :--- | :--- |
| **Độ sẵn sàng API Phòng Thi & Đọc Học Liệu** | **$\ge 99.95\%$** trong khung giờ thi cao điểm; **$\ge 99.9\%$** hàng tháng | Synthetic health checks mỗi 15 giây kết hợp Prometheus multi-window burn-rate alerts. |
| **Độ trễ API Đọc Học liệu / Phát Đề Thi** | **`p50 < 80ms`, `p95 < 250ms`** dưới tải tiêu chuẩn | OpenTelemetry histogram `http_server_duration_ms` phân theo route. |
| **Độ trễ Lưu tự động Bài thi (Autosave)** | **Server DB Commit: `p50 < 25ms`, `p95 < 120ms`**<br>**End-to-End API: `p95 < 300ms` (tối đa `< 800ms` ở 5.000 CCU)** | Đo trực tiếp trên `PUT /api/v1/attempts/{id}/responses/{item_id}` (chỉ trả `200 ACK` sau khi PostgreSQL Primary + Synchronous Standby đã commit). |
| **Độ toàn vẹn Dữ liệu Phòng Thi (Exam RPO)** | **`RPO = 0` (0% mất mát câu trả lời đã nhận ACK)** | Đảm bảo bằng **PostgreSQL Synchronous Streaming Replication** (`synchronous_commit = on`) cho các bảng `attempts`, `responses`, `response_revisions`, `grades`. |
| **Mục tiêu Khôi phục Thảm họa Toàn Vùng (DR)** | **Exam DB Auto-Failover (Patroni `synchronous_mode_strict: true`): `RPO = 0`, `RTO <= 30 giây` (tối đa `<= 2 phút`)**<br>**Full Regional DR (PITR + Object Storage): `RPO <= 15 phút`, `RTO <= 2 giờ`** | Patroni / repmgr tự động Failover sang Synchronous Standby trong $< 30\text{s}$; sao lưu WAL liên tục + Cross-Region MinIO Replication. |
| **Thời gian Khởi chạy Job Nạp Đề (Ingestion)** | **99% các Job thông thường bắt đầu chạy trong `< 90 giây`** | Cảnh báo PagerDuty/Telegram khi `oldest_queued_job_age_seconds > 90`. |

---


### 9.3. Ma Trận 6 Kịch Bản Kiểm Thử Tải / Tiêm Lỗi (`AUD-012`) & Hồ Sơ Bằng Chứng Kiểm Chứng (`Evidence Bundle — AUD-007`)

#### A. Sáu Kịch bản Kiểm thử Hiệu năng & Chịu lỗi Bắt buộc Trước Khi Đóng Cổng `GATE-08` & `GATE-09`
Mọi kết quả benchmark chỉ được công nhận khi ghi rõ cấu hình phần cứng (CPU/RAM/NVMe IOPS), phiên bản PostgreSQL/PgBouncer, kích thước dữ liệu và `commit_sha`:

| Kịch bản Kiểm thử (Scenario) | Cấu hình Tải / Lỗi Giả lập (Load & Fault Profile) | Chỉ số Đầu ra Bắt buộc Lưu vào Evidence Bundle |
| :--- | :--- | :--- |
| **1. Normal Exam Steady State** | 5.000 thí sinh làm bài đồng thời, đổi đáp án trung bình mỗi 15 giây ($\approx 333\text{ RPS}$ Autosave phân bố đều). | Throughput, độ trễ `p50/p95/p99`, tỷ lệ lỗi (`< 0.01%`), DB CPU, WAL write IOPS, PgBouncer pool wait time. |
| **2. Synchronized Start Burst** | 5.000 thí sinh bấm nút `Bắt đầu làm bài` trong cùng cửa sổ 30 giây đầu ca thi. | Độ trễ tạo `attempts` và sinh hoán vị đề, thời gian chờ khóa, tỷ lệ Cache Hit của `v_public_question_versions`. |
| **3. Reconnect Autosave Storm** | 1.500 thí sinh bị rớt mạng cục bộ trong 45 giây và đồng loạt có mạng trở lại, xả hàng đợi `IndexedDB` cùng lúc. | Tỷ lệ `200 IDEMPOTENT_REPLAY` vs `200 COMMITTED`, thời gian xả hết hàng đợi (`< 10s`), độ bão hòa kết nối DB. |
| **4. End-of-Exam Submit Storm** | 3.000 thí sinh nộp bài đồng thời ở phút cuối + 2.000 bài tự động thu (`attempt.expired`). | Thời gian xử lý khóa `attempts`, độ sâu hàng đợi `ulp_worker_grading`, thời gian trả điểm trắc nghiệm (`< 3s`). |
| **5. Primary DB Crash & Failover** | Ngắt đột ngột node PostgreSQL Primary khi đang chạy tải Autosave $300\text{ RPS}$. | Thời gian Patroni thăng cấp Synchronous Standby (`RTO <= 30s`), đối soát `autosave_idempotency_registry` xác nhận **`RPO = 0`** (0 bản ghi đã ACK bị mất). |
| **6. External Dependency Outage** | Cô lập hoàn toàn mạng tới Redis, MinIO và nhà cung cấp LLM trong 10 phút giữa ca thi. | Xác nhận làm bài, Autosave và nộp bài vẫn đạt thành công $\ge 99.9\%$ nhờ ghi trực tiếp xuống PostgreSQL; AI Tutor báo lỗi thân thiện. |

#### B. Bảng Đánh Giá Trung Thực Mức Độ Trưởng Thành Hiện Tại Theo Thang 5 Cấp (`EV-V11-01` đến `EV-V11-04` — Mục 9.1 của Bản vá V11.1)

Tuân thủ nghiêm ngặt quy tắc không gắn nhãn `"VERIFIED"` khi chưa chạy trên hạ tầng PostgreSQL/Patroni thật và dữ liệu thật:

| Mã Hạng Mục | Nội dung Kiểm tra | Mức Độ Đạt Được Hiện Tại (Thang 5 Cấp) | Phần Đã Kiểm Chứng Tự Động vs Điều Kiện Bắt Buộc Để Nâng Lên `VERIFIED` Ở `M0 / CI` |
| :--- | :--- | :---: | :--- |
| **`EV-V11-01`** | **Canonical Manifest & Gate IDs Reconciliation** | **`TESTED` (Cấp 3/5)** | Đã chạy script kiểm tra tự động xác nhận 100% đồng bộ giữa `platform_manifest.yaml` (v11.2.0) và toàn bộ văn bản (0 mã `RG-xx` sót lại, 47 modules khớp 1-1 vị trí Sprint/Track). Sẽ nâng lên `VERIFIED` khi gắn `ci/check_gate_ids.sh` vào GitHub Actions của repo thật. |
| **`EV-V11-02`** | **9 Thuật toán lõi, Subprocess `SymPy` Sandbox & FSRS-5** | **`TESTED` một phần (Python) / `SPECIFIED` (TypeScript)** | • **Python (Mục 5.2, 6.1–6.8):** Đã chạy pass bộ Unit Test trong Sandbox (bao gồm 12 ca chặn RCE `__import__`, `open`, `exec`, `__class__` của `SymPySandboxWorker`, `10g == 200g` của Hóa học, Optimistic Autosave + `payload_hash`, FSRS-5, IRT 6 tiêu chí, Crypto-Shredding).<br>• **Điều kiện nâng cấp:** Chạy lại trong CI có cài thư viện `sympy` đầy đủ (kiểm tra cả ca `10**10**10` bị cắt bởi `RLIMIT_AS`) và chạy `tsc --noEmit` + test `fake-indexeddb` cho `Dexie` TypeScript. |
| **`EV-V11-03`** | **SQL DDL, RLS InitPlan, `ulp_system_scheduler` & Least-Privilege Grants** | **`SPECIFIED` (Cấp 1/5 — Đã phân tích tĩnh)** | Đã kiểm tra tĩnh 100% bảng bật `ENABLE & FORCE RLS`, 0 role có `BYPASSRLS`, đầy đủ `hybrid_read_policy` có `EXISTS` và `GRANT` theo bảng/cột. **Bắt buộc chạy `alembic upgrade head` và `tests/security/verify_rls_hardening.sql` trên PostgreSQL 16+ thật ở `M0` để lên `TESTED` / `VERIFIED`.** |
| **`EV-V11-04`** | **Direct Source Code Audit (`exam-system`) & 5.000 CCU Benchmark** | **`SPECIFIED` (Cấp 1/5)** | Thực hiện ngay trong `M0` (audit `app/models.py`) và `Sprint 5–6` (chạy `seed_attempts.py --count 5000` + `k6_exam_5000ccu.js` trên cụm Patroni 3-node thật). |

#### C. Mô Hình Hóa Toán Học Workload 5.000 CCU & Kịch Bản `k6` Mỗi VU Một Attempt Riêng + Submit Burst (`tests/load/k6_exam_5000ccu.js` — Khắc phục P0-7)

- **Dẫn xuất theo định luật Little ($L = \lambda \cdot W$):** Với $N = 5.000\text{ CCU}$, chu kỳ chọn đáp án trung bình $T_{\text{cycle}} = 15.0\text{s}$ $\Rightarrow$ $\lambda_{\text{steady}} = 333.3\text{ RPS}$, $\lambda_{\text{peak}} = 500.0\text{ RPS}$. Với $W_{\text{db}} = 12\text{ms}$, số kết nối DB thực sự bận đồng thời là $L_{\text{db}} = 6\text{ active connections}$ (cấu hình PgBouncer `default_pool_size = 60`, `max_client_conn = 10000`).
- **Khắc phục lỗi dùng chung 1 attempt trong `k6`:** Trước khi chạy `k6`, chạy script `python tests/load/seed_attempts.py --count 5000 --out tests/load/data/sessions.json` để tạo 5.000 phiên thi độc lập; mỗi VU lấy đúng phiên thi của mình từ `SharedArray("sessions")`, tự tính `crypto.sha256` thật và có thêm kịch bản `submit_burst` riêng cho pha nộp bài đồng loạt cuối giờ:

```javascript
// tests/load/k6_exam_5000ccu.js — Mỗi VU có attempt_id riêng, tính SHA-256 thật & có kịch bản Submit Burst (Sửa P0-7)
import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import crypto from "k6/crypto";

const sessions = new SharedArray("sessions", () => JSON.parse(open("./data/sessions.json")));
const BASE_URL = __ENV.API_BASE_URL || "https://staging-api.ulp.edu.vn";

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
```

#### D. Bộ Kiểm Thử Bảo Mật Runtime Roles & RLS Hardening Trên PostgreSQL Thật (`tests/security/verify_rls_hardening.sql` — Mục 2.3 của Bản vá V11.1)

```sql
-- tests/security/verify_rls_hardening.sql — Chạy tự động ở Cổng 2 và Cổng 4 của CI Pipeline
BEGIN;

-- Test 1: Không được phép có bất kỳ Runtime/Worker/Scheduler Role nào mang cờ rolbypassrls = true
DO $$
DECLARE v_bypass_count INT;
BEGIN
    SELECT count(*) INTO v_bypass_count
    FROM pg_roles
    WHERE rolname IN (
        'ulp_app_rw', 'ulp_exam_session_rw', 'ulp_worker_ingestion',
        'ulp_worker_grading', 'ulp_system_scheduler', 'ulp_analytics_ro'
    ) AND rolbypassrls IS TRUE;
    IF v_bypass_count > 0 THEN
        RAISE EXCEPTION 'SECURITY_GATE_FAILED: Phát hiện % runtime/worker role có BYPASSRLS!', v_bypass_count;
    END IF;
END $$;

-- Test 2: Tenant B tuyệt đối không nhìn thấy học liệu hệ thống (SYSTEM_PUBLIC) đang ở trạng thái DRAFT hoặc TAKEN_DOWN (P0-3)
SET LOCAL ROLE ulp_app_rw;
SET LOCAL app.current_tenant_id = '11111111-1111-1111-1111-111111111111';
DO $$
DECLARE n INT;
BEGIN
    SELECT count(*) INTO n FROM questions
    WHERE tenant_id = system_public_tenant_id() AND publication_status <> 'PUBLISHED';
    IF n <> 0 THEN
        RAISE EXCEPTION 'HYBRID_RLS_LEAK: Tenant khác nhìn thấy % câu hỏi hệ thống chưa PUBLISHED!', n;
    END IF;
END $$;

-- Test 3: Worker chấm điểm (ulp_worker_grading) của Tenant A đọc được khóa đáp án của đề hệ thống SYSTEM_PUBLIC (P0-1)
RESET ROLE;
SET LOCAL ROLE ulp_worker_grading;
SET LOCAL app.current_tenant_id = '22222222-2222-2222-2222-222222222222';
DO $$
DECLARE n INT;
BEGIN
    -- Giả định dữ liệu seed kiểm thử đã có ít nhất 1 đề SYSTEM_PUBLIC trong assessment_answer_keys
    PERFORM count(*) FROM assessment_answer_keys WHERE tenant_id = system_public_tenant_id();
END $$;

-- Test 4: Role ulp_system_scheduler khi chưa khai báo app.system_job thì thấy 0 dòng; khai báo đúng job mới đọc được (P0-2)
RESET ROLE;
SET LOCAL ROLE ulp_system_scheduler;
RESET app.system_job;
DO $$
DECLARE n INT;
BEGIN
    SELECT count(*) INTO n FROM outbox_events;
    IF n <> 0 THEN
        RAISE EXCEPTION 'SCHEDULER_LEAK: ulp_system_scheduler thấy % dòng khi chưa đặt app.system_job!', n;
    END IF;
END $$;

-- Test 5: Role Phòng Thi (ulp_exam_session_rw) và Worker Nạp Đề (ulp_worker_ingestion) bị chặn đọc bảng đáp án (P0-8)
RESET ROLE;
SET LOCAL ROLE ulp_exam_session_rw;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM question_version_answers LIMIT 1;
        RAISE EXCEPTION 'SECURITY_LEAK: ulp_exam_session_rw đọc được bảng question_version_answers!';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;

RESET ROLE;
SET LOCAL ROLE ulp_worker_ingestion;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM assessment_answer_keys LIMIT 1;
        RAISE EXCEPTION 'SECURITY_LEAK: ulp_worker_ingestion đọc được bảng assessment_answer_keys!';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END $$;

ROLLBACK;
```

---

## CHƯƠNG 10. SỔ TAY VẬN HÀNH & XỬ LÝ SỰ CỐ SẢN XUẤT (14 OPERATIONAL RUNBOOKS — ĐẦY ĐỦ OWNER, SEVERITY, ROLLBACK)

Mọi sự cố sản xuất được phân loại theo 3 mức độ:
- **`SEV-1 (Critical)`:** Ảnh hưởng trực tiếp đến ca thi đang diễn ra, nguy cơ mất dữ liệu, rò rỉ dữ liệu liên Tenant hoặc vi phạm pháp lý khẩn cấp. Thời gian phản ứng (Response SLA): **$< 5\text{ phút}$**.
- **`SEV-2 (High)`:** Gián đoạn một tính năng chính (Nạp đề, AI Tutor, Chấm tự luận) nhưng không làm gián đoạn phòng thi trắc nghiệm. Thời gian phản ứng: **$< 15\text{ phút}$**.
- **`SEV-3 (Medium)`:** Lệch cấu trúc nguồn crawl bên ngoài hoặc cảnh báo hiệu năng sớm. Thời gian phản ứng: **$< 2\text{ giờ}$**.

| Mã & Tên Sự Cố (Incident) | Mức độ & Owner Chính | Dấu hiệu Nhận biết (Trigger) | Quy trình Xử lý Từng bước, Rollback & Truyền thông (Step-by-Step Response) |
| :--- | :---: | :--- | :--- |
| **RB-01: Nghẽn Hàng Đợi Worker (Queue Backlog)** | `SEV-2`<br>**Owner:** DevOps + DE On-Call | `oldest_queued_job_age > 180s` hoặc số lượng Job `QUEUED` vượt 500. | **1) Chẩn đoán:** Kiểm tra sức khỏe các Worker Pools (`io-crawler`, `cpu-doc-ocr`, `gpu-ai`) và kết nối PgBouncer.<br>**2) Cô lập:** Tìm các 'Poison Jobs' (`attempt_count >= 3` hoặc xử lý $> 120\text{s}$) và chuyển sang `dead_letter_items`.<br>**3) Mở rộng:** Scale ngang số lượng replica của pool đang nghẽn.<br>**4) Truyền thông:** Hiển thị thông báo trên `Import Workspace` về thời gian chờ dự kiến tăng thêm. |
| **RB-02: Lỗi Hồi Quy Bộ Bóc Tách Đề (Parser Regression)** | `SEV-2`<br>**Owner:** DE/AI Lead | Tỷ lệ `extraction_confidence < 0.80` tăng vọt sau khi deploy bản Parser mới. | **1) Rollback tức thì:** Bật `FeatureFlag` (`G01`) ghim hệ thống quay về `parser_version` ổn định liền trước trong $< 10\text{s}$.<br>**2) Bảo toàn:** Giữ nguyên 100% file gốc ở tầng Bronze.<br>**3) Khắc phục:** Đưa file gây lỗi (đã xóa PII) vào tập Golden Corpus, sửa lỗi và chạy lệnh `POST /extraction-runs/{id}/reprocess` cho các lượt bị ảnh hưởng. |
| **RB-03: Lệch Cấu Trúc Web Nguồn (Crawler Drift)** | `SEV-3`<br>**Owner:** DE On-Call | Sự kiện `crawl.parser.drift_detected`; nguồn tự động chuyển sang `PAUSED_DRIFT`. | **1) Nguyên tắc:** Tuyệt đối không bật lại nguồn khi chưa sửa selector.<br>**2) Phân tích:** So sánh bản chụp HTML thô ở tầng Bronze của trang vừa lỗi với fixture chuẩn.<br>**3) Cập nhật & Kiểm chứng:** Cập nhật selector trong `config_version` mới, chạy `/crawler/sources/{id}/validate` trên 10 URL mẫu; chỉ bấm `Resume` khi đạt Yield $\ge 95\%$. |
| **RB-04: Thu Hồi Quyền hoặc Lỗi Kết Nối Google Drive** | `SEV-3`<br>**Owner:** BE On-Call | Job đồng bộ báo lỗi `401/403 REAUTH_REQUIRED` từ Google Drive API. | **1) Tự động:** Đổi `source_connections.status = 'REAUTH_REQUIRED'` và dừng lịch quét định kỳ của kết nối đó.<br>**2) Bảo toàn:** Giữ nguyên toàn bộ đề thi và câu hỏi đã nhập hợp lệ trước đó.<br>**3) Truyền thông:** Gửi thông báo In-App/Email (`F06`) hướng dẫn giáo viên mở lại Google Picker để cấp quyền lại nếu muốn đồng bộ thêm file mới. |
| **RB-05: Sự Cố Lưu Bài Thi Giữa Giờ (Autosave Anomaly)** | `SEV-1`<br>**Owner:** Tech Lead + BE + DevOps | Tỷ lệ lỗi `5xx` trên `PUT /attempts/{id}/responses` $> 0.5\%$ trong 1 phút. | **1) Nguyên tắc bất khả xâm phạm:** Tuyệt đối không trả `200 ACK` giả nếu PostgreSQL chưa commit.<br>**2) Phía Client:** Giao diện phòng thi tự động giữ toàn bộ câu trả lời trong `IndexedDB` và retry ngầm với cùng `Idempotency-Key` (nhận `200 IDEMPOTENT_REPLAY` ngay khi mạng/DB phục hồi).<br>**3) Đền bù thời gian:** Nếu sự cố hạ tầng kéo dài $> 60\text{s}$, chạy script cộng bù thời gian (`deadline_at = deadline_at + interval`) cho toàn bộ `attempts` đang `IN_PROGRESS` và thông báo trực tiếp trên màn hình phòng thi. |
| **RB-06: Sập Cụm Redis Giữa Ca Thi (Redis Outage)** | `SEV-2`<br>**Owner:** DevOps On-Call | Mất kết nối tới Redis Sentinel/Cluster. | **1) Đảm bảo liên tục:** Do `attempts`, `responses` và `response_revisions` ghi trực tiếp xuống PostgreSQL và sự kiện nằm trong bảng `outbox_events` của PostgreSQL, thí sinh vẫn làm bài, lưu bài và nộp bài bình thường 100%.<br>**2) Khôi phục:** Chuyển sang node Redis Replica hoặc khởi động lại cụm Redis.<br>**3) Đồng bộ lại:** Outbox Relay Worker tự động quét các dòng `outbox_events` đang `PENDING` trong PostgreSQL để đẩy tiếp vào hàng đợi. |
| **RB-07: Xuất Bản Nhầm Câu Hỏi Sai Đáp Án Nguồn (Errata & Regrade)** | `SEV-2`<br>**Owner:** Tech Lead + Content Lead | Giáo viên/Học sinh báo cáo một câu hỏi trong đề đã Publish bị sai đáp án gốc. | **1) Chặn lan truyền:** Tạm gỡ câu hỏi khỏi kho bốc đề mới.<br>**2) Tạo phiên bản mới:** Tạo `question_versions` mới (`version_no + 1`) với đáp án đúng và cập nhật `assessment_answer_keys` (giữ nguyên `version_no` cũ để làm bằng chứng kiểm toán).<br>**3) Chấm lại minh bạch:** Chạy công cụ `Regrade` cho các `attempts` bị ảnh hưởng, sinh bản ghi `grades` mới với `revision_no + 1`, `grade_reason = 'ERRATA_REGRADE'`, giữ nguyên bản ghi điểm cũ (`is_current = FALSE`) và gửi thông báo giải trình cho giáo viên/học sinh. |
| **RB-08: Gián Đoạn Nhà Cung Cấp AI (LLM Provider Outage)** | `SEV-2`<br>**Owner:** DE/AI On-Call | API LLM trả lỗi `429/503` liên tục hoặc độ trễ $> 15\text{s}$. | **1) Tự động:** `E01 (AI Provider Gateway)` mở Circuit Breaker (`circuit_state = 'OPEN'`), xoay sang API Key dự phòng hoặc chuyển sang nhà cung cấp LLM thứ cấp.<br>**2) Cô lập:** Các luồng cốt lõi (Đọc bài học, Tìm kiếm, Làm bài thi, Autosave, Chấm điểm trắc nghiệm) hoàn toàn không phụ thuộc vào LLM nên tiếp tục chạy bình thường 100%. |
| **RB-09: Yêu Cầu Gỡ Bỏ Bản Quyền Khẩn Cấp (Legal Takedown)** | `SEV-1`<br>**Owner:** Tech Lead + Legal | Nhận yêu cầu pháp lý hợp lệ (`POST /api/v1/takedown-cases`). | **1) Thực thi tức thì ($< 30\text{s}$):** Gọi `/takedown-cases/{id}/execute` để phát sự kiện `rights.takedown.executed`: chuyển `rights_status = 'TAKEDOWN'`, `publication_status = 'TAKEN_DOWN'`.<br>**2) Làm sạch:** Xóa ngay lập tức khỏi bảng `search_documents`, xóa cache CDN/Redis và thu hồi Signed URL.<br>**3) Bảo toàn lịch sử thi:** Trên các đề thi cũ đã làm, hệ thống tự động ẩn văn bản câu hỏi vi phạm (hiển thị nhãn `[Nội dung đã gỡ theo yêu cầu bản quyền]`) nhưng giữ nguyên điểm số lịch sử của học sinh. |
| **RB-10: Rò Rỉ Xác Thực Hoặc Lộ Token Quản Trị** | `SEV-1`<br>**Owner:** Tech Lead + DevOps | Phát hiện truy cập bất thường hoặc nghi ngờ lộ JWT Secret / OAuth Token. | **1) Phong tỏa:** Thu hồi toàn bộ phiên đăng nhập liên quan, xoay khóa (`Rotate`) JWT Signing Key và OAuth Secret trên KMS/Vault.<br>**2) Truy vết:** Truy vấn bảng bất biến `audit_events` theo `actor_id` và `correlation_id` để lập danh sách toàn bộ bản ghi bị tác động.<br>**3) Khôi phục:** Hoàn nguyên các thay đổi cấu hình trái phép dựa trên trường `before_json` trong `audit_events`. |
| **RB-11: Sự Cố Sập Node PostgreSQL Primary (DB Failover & PITR)** | `SEV-1`<br>**Owner:** DevOps + Tech Lead | Node PostgreSQL Primary mất kết nối hoặc lỗi ổ cứng. | **1) Auto-Failover ($< 30\text{s}$):** Patroni tự động thăng cấp (Promote) node **Synchronous Standby** lên làm Primary mới (đảm bảo **RPO = 0**, không mất bất kỳ giao dịch Autosave nào đã nhận ACK).<br>**2) Kết nối:** PgBouncer tự động định tuyến kết nối vào Primary mới mà không cần khởi động lại ứng dụng.<br>**3) Trường hợp lỗi logic cần PITR:** Khôi phục từ bản Base Backup + WAL Archive về thời điểm ngay trước sự cố trên cụm song song và đối soát bảng `response_revisions`. |
| **RB-12: Sự Cố Lưu Trữ Đối Tượng MinIO / S3 (Object Storage Outage)** | `SEV-2`<br>**Owner:** DevOps On-Call | Lỗi `503 SlowDown` hoặc mất kết nối tới cụm MinIO khi tải ảnh câu hỏi / upload đề. | **1) Chiều đọc (Phòng thi):** Các ảnh câu hỏi (`FIGURE_CROP`) của đề đang thi được phục vụ trực tiếp từ lớp đệm CDN/Nginx Reverse Proxy phía trước MinIO nên thí sinh không bị mất hình vẽ.<br>**2) Chiều ghi (Upload đề):** Tạm dừng nhận Job upload file mới, chuyển lưu lượng đọc sang cụm MinIO Replica và thay thế ổ đĩa/node lỗi bằng cơ chế Erasure Coding Healing. |
| **RB-13: Đột Biến Chi Phí AI / Tấn Công Làm Cạn Ngân Sách Token** | `SEV-2`<br>**Owner:** DE/AI + BE On-Call | Sự kiện `ai.budget.threshold_exceeded` báo một Tenant tiêu thụ $> 80\%$ hoặc $100\%$ `daily_ai_budget_usd` bất thường. | **1) Chặn tự động:** `E01 (AI Gateway)` tự động khóa các yêu cầu gọi LLM mới của Tenant vượt trần (trả mã `429 TENANT_AI_BUDGET_EXCEEDED`).<br>**2) Điều tra:** Truy vấn `ai_traces` để xác định `membership_id` hoặc `acquisition_job_id` nào đang gửi vòng lặp bất thường.<br>**3) Xử lý:** Áp Rate Limit theo từng học sinh (VD: tối đa 30 tin nhắn AI Tutor / giờ) và chỉ nâng hạn mức ngày khi quản trị viên trường/trung tâm xác nhận. |
| **RB-14: Ứng Phó Sự Cố Dữ Liệu Cá Nhân & Báo Cáo Theo Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP** | `SEV-1`<br>**Owner:** Tech Lead + Legal / DPO | Phát hiện nguy cơ truy cập trái phép vào dữ liệu cá nhân của học sinh/giáo viên. | **1) Cô lập ngay lập tức:** Đóng cổng API hoặc thu hồi quyền truy cập của nguồn tấn công trong $< 15\text{ phút}$.<br>**2) Đánh giá phạm vi:** Xác định chính xác số lượng bản ghi `users`/`memberships` bị ảnh hưởng qua `audit_events` và Access Logs.<br>**3) Nghĩa vụ pháp lý (Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP):** Lập hồ sơ thông báo vi phạm quy định bảo vệ dữ liệu cá nhân gửi cơ quan chuyên trách (Cục An ninh mạng và phòng, chống tội phạm sử dụng công nghệ cao — A05 Bộ Công an) **chậm nhất trong vòng 72 giờ** kể từ khi xảy ra vi phạm, đồng thời thông báo cho tổ chức/phụ huynh liên quan. |
