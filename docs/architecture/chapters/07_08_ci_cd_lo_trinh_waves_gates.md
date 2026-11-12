# PHẦN III (TẬP 3 / 3 — V11.3): QUY TRÌNH GIT/CI/CD, LỘ TRÌNH 3 RELEASE TRACKS (M0 + SPRINT 1–14 = 30 TUẦN), 11 RELEASE GATES (GATE-01..10), GOLDEN CORPUS, SLOs & 14 RUNBOOKS VẬN HÀNH

## CHƯƠNG 7. QUY TRÌNH PHÒNG DEV, TRUNK-BASED GIT FLOW, CI/CD 6 CỔNG, DoR/DoD & ZERO-DOWNTIME MIGRATION

### 7.1. Quy mô Đội Ngũ (Team Topology — 10 Nhân sự) & Phân vai

| Vai trò (Role) | Số lượng | Phụ trách chính (Ownership) | Tiêu chuẩn bàn giao Code hàng ngày |
| :--- | :---: | :--- | :--- |
| **Tech Lead / Architect** | `1` | Duyệt thiết kế DB Migration, API/Event Contracts, Review PR nhạy cảm (RLS, Autosave, Scoring, Rights, AI Gateway). | Không merge bất kỳ PR nào vi phạm `.importlinter`, thiếu `tenant_id` RLS hoặc thiếu Unit/Integration Test. |
| **Backend Engineers (BE)** | `3` | Phát triển các domain `identity`, `taxonomy`, `content`, `questions`, `assessment`, `attempts`, `grading`, `classes`, `commerce`. | 100% endpoint tuân thủ OpenAPI 3.1, thiết lập `SET LOCAL app.current_tenant_id`, hỗ trợ `Idempotency-Key` và có test `pytest`. |
| **Data / AI Engineers (DE/AI)** | `2` | Phát triển `acquisition`, `document_intelligence` (`pypdfium2`/`pdfplumber`/LibreOffice), `crawler`, `drive_sync`, `ai_gateway`, `sympy_verifier`. | Mọi parser/model phải lưu `version`; không bao giờ ghi đè file gốc ở tầng Bronze; kiểm soát trần ngân sách token theo Tenant. |
| **Frontend Engineers (FE)** | `2` | Xây dựng 6 Workspaces (Learner, Teacher, Content Studio, Review Queue Side-by-Side, Exam Builder, Admin Ops). | Render công thức KaTeX mượt mà; xử lý đủ 6 trạng thái UI (Loading, Empty, Error, Locked Preview, Offline Retry, Conflict). |
| **QA / SDET** | `1` | Quản lý bộ Golden Corpus (11+ đề mẫu ẩn danh trên S3 Private + tập Held-out 30 đề), tự động hóa E2E Playwright, kiểm thử tải Autosave và IDOR. | Chặn đứng mọi bản build nếu Precision/Recall bóc tách Golden Corpus tụt giảm hoặc có lỗi bảo mật Cross-Tenant (IDOR). |
| **DevOps / SRE** | `1` | Vận hành CI/CD, PostgreSQL Synchronous Replication (`RPO = 0`), PgBouncer, MinIO, Redis, Prometheus/Grafana và diễn tập Backup/Restore. | Đảm bảo thời gian chạy CI Pipeline $< 8\text{ phút}$ và tự động hóa kiểm tra 10 Release Gates trước khi lên Production. |

### 7.2. Quy trình Trunk-Based Git Flow & 6 Cổng Kiểm Soát CI/CD (Khắc phục P0-1)

1. **Chiến lược nhánh (Trunk-Based Development with Short-Lived Branches):**
   - Nhánh chính `main` luôn ở trạng thái sẵn sàng triển khai (Deployable).
   - Mọi tính năng phát triển trên nhánh ngắn hạn `feat/<module>-<ticket>` (tồn tại tối đa 2–3 ngày), tích hợp qua Pull Request và ẩn dưới `FeatureFlag` (G01) nếu chưa hoàn thiện toàn bộ luồng.
2. **Sáu Cổng Tự Động trong CI Pipeline (GitHub Actions / GitLab CI):**
   - **Cổng 1 — Static & Architecture Boundary Check:** Chạy `ruff`, `mypy --strict` và `lint-imports` (cấu hình `.importlinter` cấm các module import trực tiếp SQLAlchemy model của module khác).
   - **Cổng 2 — Schema & RLS Compliance Check:** Chạy `alembic upgrade head` trên PostgreSQL 16 container sạch, sau đó chạy script kiểm tra mọi bảng nghiệp vụ đều đã bật `ROW LEVEL SECURITY` và `FORCE ROW LEVEL SECURITY`.
   - **Cổng 3 — Unit & Deterministic Contract Tests:** Chạy toàn bộ test đơn vị (Chấm điểm THPTQG 2025+, Idempotency-First Autosave, FSRS v5, `structural_hash` Hóa học, `SymPy` Verifier).
   - **Cổng 4 — Multi-Tenant IDOR Security Suite:** Chạy bộ test tự động tạo dữ liệu ở `Tenant A` và dùng JWT của `Tenant B` gọi toàn bộ endpoints đọc/sửa/xóa (bắt buộc trả về `404`/`403` 100%).
   - **Cổng 5 — Golden Corpus Extraction Gate:** Kéo 11+ đề mẫu đã ẩn danh từ bucket `s3://ulp-qa-golden-private/`, chạy `pytest -m golden` kiểm tra Precision $\ge 98\%$, Recall $\ge 96\%$.
   - **Cổng 6 — OSS License & Vulnerability Audit:** Chặn mọi thư viện vi phạm giấy phép AGPLv3 (nếu không nằm trong danh sách đã mua giấy phép thương mại) và quét CVE bằng `pip-audit` / `trivy`.

### 7.3. Tiêu chuẩn Definition of Ready (DoR) & Definition of Done (DoD)

* **Definition of Ready (DoR — Điều kiện trước khi đưa Ticket vào Sprint):**
  1. Đã xác định rõ mã Module (`A01`–`G06`) và bảng dữ liệu sở hữu ghi duy nhất (Write Owner).
  2. Đã định nghĩa rõ hợp đồng API (OpenAPI/Pydantic v2) hoặc hợp đồng sự kiện (`event_type`, `schema_version`).
  3. Đã xác định rõ yêu cầu phân quyền (`Permission`, `Entitlement`, chính sách RLS) và trạng thái trên 5 trục trạng thái.
  4. Có tiêu chí nghiệm thu định lượng (Acceptance Criteria) và dữ liệu kiểm thử mẫu.
* **Definition of Done (DoD — Điều kiện hoàn thành Ticket):**
  1. Code vượt qua cả 6 Cổng CI/CD, độ phủ test (Coverage) của logic nghiệp vụ lõi $\ge 85\%$.
  2. Mọi thao tác thay đổi dữ liệu nhạy cảm đều ghi `audit_events` và phát `outbox_events` trong cùng Transaction.
  3. Đã kiểm thử thực tế trên môi trường Staging với PgBouncer `transaction` pooling bật RLS.
  4. Có đầy đủ Metrics (OpenTelemetry/Prometheus) và tài liệu cập nhật vào Runbook nếu có cảnh báo mới.

### 7.4. Quy trình Database Migration 5 Bước Không Gián Đoạn (Zero-Downtime Migration)

1. **Bước 1 (Expand):** Thêm bảng mới hoặc cột mới (cho phép `NULL` hoặc có `DEFAULT` hằng số không khóa bảng); tạo chỉ mục bằng `CREATE INDEX CONCURRENTLY`.
2. **Bước 2 (Deploy Dual-Write):** Triển khai code mới ghi đồng thời vào cả cấu trúc cũ và cấu trúc mới (ví dụ khi tách bảng `questions` sang `question_versions` và `assessment_answer_keys`).
3. **Bước 3 (Backfill):** Chạy Worker chuyển đổi dữ liệu lịch sử theo từng lô nhỏ (`batch_size = 500`), đối soát tổng số dòng và mã băm `sha256`.
4. **Bước 4 (Switch Read):** Chuyển luồng đọc của API sang bảng/cột mới qua cờ `FeatureFlag` sau khi dữ liệu khớp 100%.
5. **Bước 5 (Contract):** Gỡ bỏ cột/bảng cũ ở Sprint tiếp theo sau khi hệ thống vận hành ổn định tối thiểu 7 ngày.

---

## CHƯƠNG 8. LỘ TRÌNH 3 RELEASE TRACKS (`M0 + SPRINT 1–14 = 30 TUẦN` HOẶC `PHƯƠNG ÁN B = 26 TUẦN`), KẾ HOẠCH NGUỒN LỰC & 11 RELEASE GATES (`GATE-xx`)

> **Quy tắc Đồng bộ Tuyệt đối trong V11.3 (Đã tích hợp Mục 6 & Mục 7 của Bản vá V11.1):**
> 1. **Đưa `E01` về Sprint 2 và `C02` về Sprint 3 (chuyển `B07` sang Sprint 4):** Đáp ứng đúng yêu cầu của `ADR-006` để `A06/A07` có sẵn cổng AI và bộ kiểm chứng `SymPy` ngay từ Sprint 3.
> 2. **Quy tắc tải Sprint:** Tối đa **6 modules / Sprint**, trong đó tối đa **2 modules rủi ro cao** (RLS, Autosave, AI Gateway, Grading, Crawler, Drive).
> 3. **Khởi động Pilot sớm từ Sprint 8 (kèm Anchor Items):** Giúp tích lũy dữ liệu trả lời từ Sprint 8 đến Sprint 11 cho hiệu chuẩn `IRT` (`D06`); chấp nhận `RULE_WEIGHTED_L1` là trạng thái hợp lệ tại thời điểm Go-Live nếu câu hỏi chưa đạt đủ 6 tiêu chí thống kê.
> 4. **Bổ sung 4 tuần Buffer (`Sprint 13–14` ở Phương án A — 30 tuần, hoặc hoãn `A02, E03, G06` ở Phương án B — 26 tuần):** Dành riêng cho Pentest bên ngoài, WAF, sửa lỗi từ trường Pilot và diễn tập DR trước khi Go-Live.

### 8.1. Bảng Phân Công Chi Tiết `M0` và `Sprint 1 – Sprint 14` (Đội ngũ 10 Kỹ sư)

| Giai đoạn / Sprint & Modules Chính Thức | Nhiệm vụ Backend (3 BE) | Nhiệm vụ Data/AI & Worker (2 DE/AI) | Nhiệm vụ Frontend (2 FE) | Kiểm thử QA/SDET & DevOps (Exit Criteria & Gates) |
| :--- | :--- | :--- | :--- | :--- |
| **`M0 — Pre-Flight`**<br>*(Tuần 1–2 — Milestone M0)*<br>**Modules:** Chuẩn bị nền tảng (0 module nghiệp vụ) | • Audit repo `exam-system` (`app/models.py`), lập kịch bản Backfill.<br>• Khởi tạo 7 DB Roles (`NOBYPASSRLS`, gồm cả `ulp_system_scheduler`), `UUIDv7`, `fts_dictionary_versions`.<br>• Lập hồ sơ DPIA & DPA nhà cung cấp. | • Chốt `PDFEngineAdapter` (`pypdfium2` + `pdfplumber`), gỡ bỏ `PyMuPDF` AGPLv3.<br>• Ẩn danh hóa 11 đề Golden + 30 đề Held-out lên bucket S3 riêng; dựng khung tính chi phí LLM/GPU. | • Khởi tạo Next.js App Router, Design System, bộ render `KaTeX` (`trust: false`) và khung 6 trạng thái UI chuẩn. | • Dựng cụm PostgreSQL 3-Node Patroni (`synchronous_mode_strict: true`) + PgBouncer + Vault Transit + MinIO + Redis + CI 6 Cổng (`check_gate_ids.sh`). |
| **`Sprint 1` (Track 1)**<br>*(Tuần 3–4 — Milestone M1)*<br>**Modules (6):** `A01, B03, F04, F05, G01, G03` | • Middleware JWT + `(SELECT current_app_tenant_id())`, RBAC (`F05`), Consent & Vault Transit Per-User Crypto-Shredding (`F04`).<br>• Upload MinIO (`B03`), Config (`G01`), Outbox/Inbox (`G03`), Ingestion (`A01`). | • Khung Worker đọc `outbox_events` qua `ulp_system_scheduler` (`app.system_job = 'outbox_relay'`).<br>• Quét ClamAV, kiểm tra `libmagic` và lưu bất biến tầng Bronze. | • Màn hình Đăng nhập, Xác nhận Phụ huynh, Chọn Workspace.<br>• Component Upload tài liệu lên MinIO có thanh tiến trình Pipeline. | • Chạy bộ `verify_rls_hardening.sql` trên PostgreSQL 16 thật.<br>• **Demo:** Upload file đề PDF, lưu tầng Bronze, kiểm chứng xóa khóa trên Vault Transit + WORM Tombstone. |
| **`Sprint 2` (Track 1)**<br>*(Tuần 5–6 — Milestone M1)*<br>**Modules (6):** `B01, B02, B04, B06, E01, G02` | • CRUD `taxonomy_nodes` (`ltree` — `B04`), `curriculum_versions` (`B06`).<br>• API `content_items` & `content_versions` bất biến (`B01`, `B02`).<br>• **Triển khai sớm `E01 (AI Provider Gateway)`** & `G02 (Legal Takedown)`. | • Cài đặt `E01`: xoay vòng API Key, `PIIScrubber` hai chiều và khóa trần `daily_ai_budget_usd`.<br>• Seed cây SGK Kết nối tri thức môn **Toán & Vật lí (Lớp 10–11–12)**. | • Giao diện **Workspace 4 (VietJack-Style Content Studio)**, **Workspace 1 (Trang đọc chuyên đề SSR)** và giao diện thực thi Takedown (`Workspace 6`). | • Kiểm tra Trigger tự bật `is_immutable = TRUE` khi Publish, kiểm tra `hybrid_read_policy` chặn bản `DRAFT`, và test Takedown `< 30s`. |
| **`Sprint 3` (Track 1)**<br>*(Tuần 7–8 — Milestone M1)*<br>**Modules (5):** `A05, A06, A07, A09, C02` | • API Parser Registry (`A05`), OCR Jobs (`A06`), Candidate Extraction (`A07`), Hàng đợi duyệt Side-by-Side (`A09`) và **Kiểm chứng Chất lượng Câu hỏi (`C02`)**. | • Đưa `VietnameseExamDocumentProcessor` (LibreOffice cô lập profile, `find_tables()`, Union-Find) và **Subprocess `SymPy` Sandbox (`sympy_sandbox_worker.py`)** vào xử lý đề Toán & Lý. | • Giao diện **Workspace 3 (Side-by-Side Reviewer)**: khung trái hiển thị trang gốc + lớp phủ `bbox` tĩnh (hoãn kéo-thả re-crop sang V1.1 nếu giữ 2 FE), khung phải duyệt câu hỏi & xem cảnh báo `SymPy`. | • Kiểm chứng 12 ca chặn RCE của Subprocess `SymPy` và độ chính xác bóc tách đề Toán/Lý.<br>• **Demo:** Nhập đề Toán 11 (`MathType OLE`) & Lý 11 -> `SymPy` kiểm chứng -> Duyệt. |
| **`Sprint 4` (Track 1)**<br>*(Tuần 9–10 — Milestone M2)*<br>**Modules (5):** `A04, A08, B07, C01, C03` | • API Nhập đề nguyên bản (`A04`), Khử trùng 3 tầng (`A08`), **Tìm kiếm FTS không dấu (`B07`)**.<br>• Ngân hàng 6 loại câu hỏi tách bảng `question_version_answers` (`C01`) & Ma trận THPTQG 2025+ (`C03`). | • Forward Chemical Tokenizer (`A08`: bắt cả số dính đơn vị `10g == 200g` và bảo toàn `H2SO4`).<br>• Bộ kiểm tra đáp ứng ma trận và xuất `ShortageReport`. | • Trình soạn thảo 6 loại câu hỏi (`ABCD`, `Đúng/Sai 4 ý`, `Trả lời ngắn`, `Tự luận`, `Câu chùm`), thanh tìm kiếm FTS và lưới thiết lập Ma trận đề. | • Kiểm tra quyền `ulp_exam_session_rw` và `ulp_worker_ingestion` bị chặn `SELECT` trên `question_version_answers`.<br>• **Demo:** Nhập nguyên đề chuyên đề Bayes (67 câu) và tìm kiếm FTS `< 120ms`. |
| **`Sprint 5` (Track 1)**<br>*(Tuần 11–12 — Milestone M2)*<br>**Modules (3):** `C04, C05, C06` | • API Bốc đề tất định (`C04`), Phát đề & Hoán vị (`C05`).<br>• API **Optimistic-Concurrency Autosave** (`C06`: kiểm tra `expected_response_version`, đối chiếu `payload_hash`, `deadline_at`, khóa chống đua lệnh `Submit` và ghi log chống gian lận). | • Worker quét hết giờ bằng `ulp_system_scheduler` (`app.system_job = 'attempt_expiry'`) rồi mở Transaction riêng dưới `ulp_worker_grading` để chốt `AUTO_SUBMITTED`. | • Giao diện **Workspace 2 (Phòng Thi Trực Tuyến)**: lưu **`Dexie` (`IndexedDB` thật)**, gửi `expected_response_version` + `payload_hash`, tự động `resync`, và ghi nhận `visibilitychange` (Anti-Cheat). | • **Chạy bộ Load Test `k6` 5.000 CCU (mỗi VU 1 attempt riêng từ `SharedArray`) & Chaos Failover Patroni**.<br>• **Demo:** Thi thử 5.000 CCU đạt Server `p95 < 120ms`, `RPO = 0`. |
| **`Sprint 6` (Track 1 Exit)**<br>*(Tuần 13–14 — Milestone M2)*<br>**Modules (1):** `C07` *(+ Chốt Track 1)* | • Engine chấm điểm tất định 3 phần (`C07`) đọc đáp án đề hệ thống qua policy `grading_read_answer_keys`, ánh xạ ngược `option_permutations_json`, `NumericParsePolicy` (`max_chars=4`) và hỗ trợ nhiều `grades` revisions khi phúc khảo/Regrade. | • Kiểm toán toàn bộ quyền `information_schema.role_table_grants` khớp 100% với `grants_manifest.csv` và đóng gói bằng chứng cho Track 1. | • Giao diện Xem kết quả thi, Giải thích đáp án sau khi mở khóa (`reveal_policy`), hiển thị cờ cảnh báo gian lận cho giáo viên và gửi yêu cầu Phúc khảo (`Appeals`). | • **Đóng các cổng nghiệm thu Track 1 (`GATE-01`, `GATE-02`, `GATE-03`, `GATE-04`, `GATE-05`, `GATE-06a`, `GATE-07`):** Phát hành bản MVP Core Toán & Vật lí THPT. |
| **`Sprint 7` (Track 2)**<br>*(Tuần 15–16 — Milestone M3)*<br>**Modules (5):** `B05, D01, D02, D03, D05` | • API Đồ thị tri thức `knowledge_edges` có chặn chu trình DAG (`B05`).<br>• API Learner Dashboard (`D01`), Lộ trình tiên quyết (`D02`), Luyện tập 4 Tab (`D03`) và Sổ tay câu sai (`D05`). | • Thuật toán gom câu sai tự động sau khi chấm điểm, duyệt đồ thị `PREREQUISITE` và tìm 3 câu biến thể cùng `structural_hash`/`pgvector` để giao bài khắc phục. | • Nhúng 4 Tab bài tập dưới trang Lý thuyết (`Workspace 1`), giao diện Sổ tay câu sai (làm đúng 2 câu liên tiếp để xóa lỗi) và Dashboard học sinh. | • Kiểm tra chặn chu trình `422` và điều kiện đóng lỗi `consecutive_correct_count >= 2`.<br>• **Demo:** Sai câu Bayes -> Sổ tay câu sai -> Ôn bài tiên quyết & làm đúng 2 câu biến thể. |
| **`Sprint 8` (Track 2 + Pilot Kickoff)**<br>*(Tuần 17–18 — Milestone M3)*<br>**Modules (4):** `F01, F02, F03, F06` | • API Quản lý Lớp học (`F01` với Soft-Delete `class_members`), Giao bài kiểm tra (`F02` với `reveal_policy`), Bảng điểm lớp (`F03`) và Thông báo chống gửi trùng (`F06`). | • Hoàn thiện bóc tách đề **Hóa học & Sinh học** (`GATE-06b`: đủ 41 đề Golden + Held-out).<br>• **Khởi động Pilot thực tế tại trường THPT** với bộ đề có gắn câu hỏi mỏ neo (`Anchor Items`) để thu thập mẫu cho IRT. | • Giao diện **Workspace 5 (Teacher LMS Hub)**: tạo lớp, mời học sinh, giao đề thi, xem phổ điểm lớp và Top 5 câu cả lớp sai nhiều nhất. | • **Đóng cổng `GATE-06b` (41 đề Toán/Lý/Hóa/Sinh)** và vận hành đợt kiểm tra đầu tiên tại trường Pilot. |
| **`Sprint 9` (Track 2 Exit)**<br>*(Tuần 19–20 — Milestone M4)*<br>**Modules (3):** `D04, E02, E04` | • API Thẻ nhớ & Lịch ôn tập `FSRS-5` (`D04`), Gia sư AI 3 chế độ có khóa phòng thi (`E02`) và Sinh lời giải từng bước kiểm tra chéo với Subprocess `SymPy` (`E04`). | • Cài đặt chuẩn 19 trọng số `FSRS-5` (Linear Damping + Mean Reversion + trần quên); đo đạc chi phí thực tế LLM/GPU từ `ai_traces` trên tập học sinh Pilot. | • Khung chat **Gia sư AI** (`Hint / Explain / Challenge`) và giao diện lật thẻ nhớ công thức `FSRS-5`. | • **Đóng cổng `GATE-08`:** Khớp 100% vector chuẩn `py-fsrs`, `SymPy` đạt $\ge 95\%$, chặn 100% AI Tutor khi đang thi (`IN_PROGRESS`). |
| **`Sprint 10` (Track 3)**<br>*(Tuần 21–22 — Milestone M5)*<br>**Modules (3):** `A02, A03, E03` | • API Permitted Web Crawler (`A02`), Google Drive Sync 2 chế độ (`Picker + drive.file` & `Service Account` — `A03`), và Sinh câu hỏi nháp bù ma trận (`E03`). | • Crawler có `PinnedIPHTTPSConnection` ghim IP vào socket ở mỗi bước Redirect + `ParserDriftMonitor`; bung nén chống Zip Bomb & ghép cặp 4 tín hiệu `Đề + HDC`. | • Giao diện chọn nhiều file đề/zip qua Google Picker, duyệt cặp `Đề + HDC`, bảng điều khiển Crawler và công cụ sinh câu hỏi bù ma trận. | • Vượt qua bộ test SSRF/DNS Rebinding, Zip Bomb và xác nhận 0% câu hỏi AI sinh ra tự động Publish khi chưa qua `A09`. |
| **`Sprint 11` (Track 3)**<br>*(Tuần 23–24 — Milestone M5)*<br>**Modules (3):** `D06, E05, E06` | • API Ước lượng năng lực `IRT` 6 tiêu chí (`D06`), Gợi ý bước học tiếp theo có giải thích (`E05`) và Trợ lý AI cho Giáo viên (`E06`). | • Chạy hiệu chuẩn `IRT` trên dữ liệu Pilot tích lũy từ **Sprint 8 đến Sprint 11**; các câu đủ 6 tiêu chí được nâng lên `L2/L3`, các câu chưa đủ mẫu giữ ở `RULE_WEIGHTED_L1` hợp lệ. | • Hộp **"Điểm ước lượng năng lực tham khảo (Khoảng tin cậy 95%)"**, thẻ gợi ý bước tiếp theo và màn hình **Teacher AI Copilot**. | • Phê duyệt báo cáo hiệu chuẩn IRT (`Calibration Diagnostics Report`) và kiểm chứng cơ chế tự động Fallback. |
| **`Sprint 12` (Track 3)**<br>*(Tuần 25–26 — Milestone M5)*<br>**Modules (3):** `G04, G05, G06` | • API Kho phân tích (`G04`), Gói cước & Thanh toán (`G05`), Cấu hình mở rộng `VerticalPack` (`G06`). | • Hoàn thiện job tổng hợp `daily_metric_rollups` chạy dưới `ulp_system_scheduler` (`app.system_job = 'metric_rollup'`). | • Trang quản trị gói cước Free/Pro, Dashboard KPI vận hành và tối ưu WCAG 2.2 AA. | • Hoàn tất toàn bộ 47 modules tính năng (nếu chọn Phương án B 26 tuần thì hoãn `A02, E03, G06` để dùng Sprint 11–12 làm Hardening). |
| **`Sprint 13 – Sprint 14` (Hardening & Go-Live Buffer)**<br>*(Tuần 27–30 — Milestone M6)*<br>**Modules:** 0 module mới (100% Hardening) | • Khắc phục toàn bộ phát hiện từ đợt Pentest độc lập và phản hồi từ giáo viên/học sinh trường Pilot; đóng băng Schema Production. | • Diễn tập khôi phục thảm họa (PITR Restore + chạy `reapply_tombstones_after_pitr_restore` + kiểm tra xóa khóa Vault Transit) và làm nóng cache. | • Kiểm thử E2E mất mạng / F5 giữa giờ thi với `Dexie` và hoàn thiện hiệu năng Mobile Web. | • **Đóng cổng `GATE-09` (Legal DPIA/DPA, OSS SBOM, Pentest) và `GATE-10` (DR Drill, Patroni Strict Quorum) -> Chính thức Go-Live.** |

### 8.2. Hai Phương Án Ngân Sách Công Sức & Thời Lượng (`Phương án A: 30 Tuần` vs `Phương án B: 26 Tuần`)

| Tiêu chí So sánh | **Phương án A (Khuyến nghị — Đầy đủ 47 Modules + 4 Tuần Buffer)** | **Phương án B (Cố định 26 Tuần — Cắt giảm phạm vi Go-Live còn 44 Modules)** |
| :--- | :--- | :--- |
| **Tổng thời lượng (Tính cả `M0`)** | **30 Tuần** (`M0: 2 tuần` + `Sprint 1..12: 24 tuần` + `Sprint 13..14 Buffer: 4 tuần`) | **26 Tuần** (`M0: 2 tuần` + `Sprint 1..10: 20 tuần` + `Sprint 11..12 Buffer: 4 tuần`) |
| **Ngân sách Công sức (10 Kỹ sư)** | `300 Gross PW` $\Rightarrow$ **`225.0 Net Effective PW`** (Focus Factor 75%) | `260 Gross PW` $\Rightarrow$ **`195.0 Net Effective PW`** (Focus Factor 75%) |
| **Phạm vi Tính năng tại Go-Live** | Trọn vẹn **47/47 Modules** (bao gồm cả Web Crawler `A02`, AI Question Gen `E03`, Vertical Packs `G06`). | **44/47 Modules** (Hoãn `A02`, `E03`, `G06` sang bản V1.1 sau Go-Live để giải phóng 30 Gross PW dành cho Pentest, DR Drill và sửa lỗi Pilot). |

### 8.3. Mười Một Cổng Nghiệm Thu Phát Hành Chuẩn Hóa (`GATE-01` đến `GATE-10` — Kiểm Tra Tự Động Bằng `ci/check_gate_ids.sh`)

Toàn bộ tài liệu và `platform_manifest.yaml` chỉ sử dụng duy nhất hệ mã **`GATE-xx`** dưới đây:

| Mã Gate | Tên Cổng Nghiệm Thu (Release Gate) | Thời điểm Đóng | Tiêu chí Kỹ thuật & Bằng chứng Bắt buộc (`Evidence Bundle`) |
| :--- | :--- | :---: | :--- |
| **`GATE-01`** | **Multi-Tenant RLS, InitPlan & Zero-IDOR Gate** | `Sprint 6` | 100% bảng nghiệp vụ bật `ENABLE & FORCE RLS` với `(SELECT current_app_tenant_id())`; vượt qua `verify_rls_hardening.sql` (chặn đọc `DRAFT` hệ thống, khớp `grants_manifest.csv`, 0 lỗi IDOR). |
| **`GATE-02`** | **Data Immutability & 5-Axis State Gate** | `Sprint 6` | Trigger DB chặn 100% lệnh `UPDATE/DELETE` trên bảng phiên bản đã khóa và bảng phân vùng Append-Only (`audit_events`, `response_revisions`); 0 học liệu `PENDING_REVIEW`/`RESTRICTED` lọt sang `PUBLISHED`. |
| **`GATE-03`** | **Exam Security & Zero-Answer-Leak Gate** | `Sprint 6` | Role `ulp_exam_session_rw` và `ulp_worker_ingestion` bị từ chối quyền đọc `question_version_answers` và `assessment_answer_keys` (`42501 insufficient_privilege`); JSON đề thi chứa 0% dữ liệu đáp án. |
| **`GATE-04`** | **Autosave Durability, Dexie Offline & Chaos Gate** | `Sprint 6` | Client lưu `IndexedDB` thật (`Dexie`), gửi đủ `expected_response_version` + `payload_hash`, tự động `resync` khi gặp `409`; đạt `RPO = 0` khi ngắt mạng, F5 trình duyệt, sập Redis và Failover Patroni. |
| **`GATE-05`** | **High-Concurrency 5.000 CCU Per-VU Load Gate** | `Sprint 6` | Kịch bản `k6` (mỗi VU 1 `attempt_id` riêng từ `SharedArray` + kịch bản `submit_burst`) đạt độ trễ Server `p50 < 25ms`, `p95 < 120ms`, `p99 < 300ms`, tỷ lệ lỗi `< 0.01%`. |
| **`GATE-06a`** | **Golden & Held-out Extraction Gate (Toán & Vật lí)** | `Sprint 6` | Đạt Precision $\ge 98\%$, Recall $\ge 96\%$ về ranh giới câu hỏi, công thức `MathType OLE` và hình vẽ Vector (có `find_tables()` bảo vệ bảng) trên toàn bộ tập đề Toán & Vật lí. |
| **`GATE-06b`** | **Golden & Held-out Extraction Gate (Hóa & Sinh — Đủ 41 Đề)** | `Sprint 8` | Đạt Precision $\ge 98\%$, Recall $\ge 96\%$ trên toàn bộ **11 đề Golden + 30 đề Held-out (41 đề đủ 4 môn Toán, Lý, Hóa, Sinh)** bao gồm sơ đồ hữu cơ, bảng phức chất và ảnh chụp nghiêng. |
| **`GATE-07`** | **Deterministic Grading & Option Permutation Gate** | `Sprint 6` | Khớp 100% điểm số trên 500 bài thi mẫu chuẩn THPTQG 2025+ (có ánh xạ ngược `option_permutations_json`, `max_chars=4`, đọc đáp án đề hệ thống qua `grading_read_answer_keys` và Regrade nhiều `revision_no`). |
| **`GATE-08`** | **AI Safety, Exam Lock, Subprocess SymPy & FSRS-5 Gate** | `Sprint 9` | Subprocess `SymPy` (`python -I`) chặn 100% mẫu RCE và đạt độ khớp nghiệm $\ge 95\%$; `FSRS-5` khớp 100% vector chuẩn `py-fsrs`; chặn 100% AI Tutor khi đang thi (`IN_PROGRESS`) và khi chạm trần `daily_ai_budget_usd`. |
| **`GATE-09`** | **Legal DPIA/DPA, Vault Erasure, OSS SBOM & Pentest Gate** | `Sprint 14` | Hoàn tất ký DPIA/DPA xuyên biên giới; kiểm chứng xóa khóa `ulp-user-{id}` trên Vault Transit + xóa văn bản tự do; 0 thư viện nhiễm AGPLv3 trong `sbom.cdx.json`; vá 100% lỗi High/Critical từ đợt Pentest độc lập. |
| **`GATE-10`** | **DR, Patroni Strict Quorum & Post-PITR Tombstone Gate** | `Sprint 14` | Diễn tập thực tế: Auto-Failover Patroni (`synchronous_mode_strict: true`) đạt `RPO = 0`, `RTO <= 30s`; diễn tập PITR Restore + chạy `reapply_tombstones_after_pitr_restore()` đạt `RTO <= 2 giờ` mà không làm sống lại PII đã xóa. |

```bash
#!/usr/bin/env bash
# ci/check_gate_ids.sh — Chạy tự động ở Cổng 1 của CI Pipeline
set -euo pipefail
if grep -rnE '\bRG-[0-9]{2}\b' docs/ handover/; then
  echo "ERROR: Phát hiện mã RG-xx cũ; bắt buộc dùng thống nhất GATE-xx theo platform_manifest.yaml"; exit 1
fi
declared=$(grep -oE 'GATE-[0-9]{2}[ab]?' platform_manifest.yaml | sort -u)
used=$(grep -rhoE 'GATE-[0-9]{2}[ab]?' docs/ handover/ | sort -u)
missing=$(comm -13 <(echo "$declared") <(echo "$used"))
[ -z "$missing" ] || { echo "ERROR: Phát hiện mã Gate không nằm trong platform_manifest.yaml: $missing"; exit 1; }
```


### 8.4. Thứ Tự Ưu Tiên Triển Khai `Wave 0 -> Wave 1 -> Wave 2` & Ánh Xạ `G0..G6` Với `GATE-01..10` (Hợp Nhất Phụ Lục Hardening V11.3)

Để đội kỹ thuật không triển khai dàn trải, các hạng mục Hardening (`H-01` đến `H-07`) được khóa cứng theo 3 đợt ưu tiên (**Wave 0 phải đóng xong trước khi mở rộng tải**):

| Đợt Triển Khai (Wave) | Mã Hardening & Hạng mục Bắt buộc | Giải pháp Đã Hợp Nhất Đồng Bộ Với Schema Chương 4 & Code Chương 6 | Ánh xạ Cổng Nghiệm thu (`G0..G6` $\leftrightarrow$ `GATE-xx`) |
| :--- | :--- | :--- | :--- |
| **Wave 0 (M0 -> Sprint 3)**<br>*Đóng toàn bộ P0 Security & Correctness* | • **`H-01` (RLS Child & Parent Visibility):** Chặn đường vòng qua bảng con.<br>• **`H-02` (SymPy Fail-Closed):** Xóa mọi fallback chạy `SymPy` trong process API.<br>• **`H-03` (Least-Privilege DB Roles):** Thu hồi `CREATE` và khóa quyền theo đúng bảng.<br>• **`H-04` (Mandatory `expected_response_version`):** Bắt buộc truyền `expected_response_version: int` (không `Optional`). | • Đã cập nhật trực tiếp vào `hybrid_read_policy` của cả 21 bảng ở Chương 4 (bao gồm kiểm tra 2 đầu mút `knowledge_edges`).<br>• Đã xóa sạch `except AttributeError: sp.sympify()` và nhánh fallback in-process ở Mục 6.3 (`SANDBOX_WORKER_UNAVAILABLE`).<br>• Đã đổi chữ ký hàm `process_autosave_idempotent_first` ở Mục 6.4 bắt buộc `expected_response_version: int`. | • **`G0 Security`** $\equiv$ `GATE-01`, `GATE-03`, `GATE-08`<br>• **`G2 Correctness`** $\equiv$ `GATE-02`, `GATE-04`, `GATE-07` |
| **Wave 1 (Sprint 3 -> Sprint 8)**<br>*Chuẩn hóa Pipeline Bronze/Silver/Gold & Observability* | • **`H-05` (Data Engineering Lineage & DQ KPIs):** Kiểm soát 6 chặng (`Source -> Bronze -> Silver -> Quality Gate -> Gold -> Serving`).<br>• **`H-06` (SLI/SLO & Observability):** Đo đủ 6 miền (API, Autosave, Ingestion, Search, SymPy/AI, Database). | • Mọi chặng lưu vết vào `pipeline_stage_runs` và `extraction_runs` kèm `input_ref`, `output_ref`, `parser_version`; hỗ trợ Replay từ tầng Bronze bất biến và fan-out lệnh Takedown trong `< 30s`. | • **`G1 Data`** $\equiv$ `GATE-06a` (Toán/Lý), `GATE-06b` (Hóa/Sinh) |
| **Wave 2 (Sprint 5 -> Sprint 14)**<br>*Kiểm thử Tải 5.000 CCU, Chaos, DR & Traceability* | • **`H-06` (Load, Chaos & Restore Drill):** Chạy `k6` mỗi VU 1 attempt riêng và diễn tập PITR + WORM Tombstone.<br>• **`H-07` (Manifest & Traceability):** Đồng bộ 100% số lượng Roles (7 DB Roles), ADRs (17 ADRs) và Gates (11 Gates). | • Đã đồng bộ toàn bộ tiêu đề Phần III, comment SQL 7 DB Roles và `platform_manifest.yaml` (v11.3.0). | • **`G3 Performance`** $\equiv$ `GATE-05`<br>• **`G4 Resilience`** $\equiv$ `GATE-10`<br>• **`G5 Legal & G6 Release`** $\equiv$ `GATE-09` |

---

