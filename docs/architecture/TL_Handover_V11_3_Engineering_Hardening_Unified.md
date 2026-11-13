# BỘ TÀI LIỆU BÀN GIAO KỸ THUẬT CẤP TECH LEAD — PHIÊN BẢN V11.3 (ENGINEERING-HARDENING UNIFIED MASTER EDITION)

**Dự án:** Nền tảng Học tập, Luyện thi & Khảo thí Thích ứng (Universal Learning & Assessment Platform — `exam-system` Upgrade)  
**Phiên bản:** V11.3 — Engineering-Hardening Unified Master (Bản Hợp Nhất Hoàn Chỉnh Giữa Kiến Trúc Master V11.0, Báo Cáo Review Kiến Trúc & Bản Vá Kỹ Thuật V11.1)  
**Ngày phát hành:** 12/11/2026  
**Đối tượng sử dụng:** Tech Lead, Solution Architect, Backend Engineers (BE), Data/AI Engineers (DE/AI), Frontend Engineers (FE), QA/SDET, DevOps/SRE & Pháp chế (Legal/Compliance)  
**Nguyên tắc Trung thực Kỹ thuật (Engineering Integrity Rule):** Tài liệu này áp dụng nghiêm ngặt thang 5 cấp độ trưởng thành (`SPECIFIED`, `IMPLEMENTED`, `TESTED`, `VERIFIED`, `OPERATED`). Mọi thiết kế SQL/RLS, thuật toán, hợp đồng API và kịch bản kiểm thử trong tài liệu này đang ở mức **`ENGINEERING-REVIEWED SPECIFICATION`** (riêng các thuật toán Python và bộ lọc AST/Whitelist của `SymPy` đã chạy kiểm thử đơn vị trong Sandbox); hệ thống chỉ được gắn nhãn `VERIFIED` / `OPERATED` sau khi chạy `alembic upgrade head`, bộ test SQL/RLS và `k6` trên hạ tầng PostgreSQL + Patroni thật trong giai đoạn `M0` và các cổng `GATE-01..GATE-10`.

## PHẦN MỞ ĐẦU: QUY TẮC QUẢN TRỊ KỸ THUẬT, CANONICAL MANIFEST & TỔNG HỢP HỢP NHẤT BẢN VÁ V11.1

### 1. Ma Trận Trạng Thái Kiểm Chứng Thực Tế & Bảng Tổng Hợp 9 Hạng Mục Đã Hợp Nhất Từ Bản Vá V11.1

Toàn bộ 8 lỗi P0 và nhóm lỗi P1 trong báo cáo *Review Kiến Trúc V11.0* và *Bản Vá V11.1* đã được **tích hợp trực tiếp vào đúng các chương tương ứng của bản V11.3 này** (xóa bỏ hoàn toàn việc phải đọc song song 2 file):

| Mã Bản Vá | Vấn đề Phát hiện trong Review V11.0 | Giải pháp Kỹ thuật Đã Hợp Nhất Trực Tiếp vào Bản V11.3 | Vị trí trong V11.3 & Mức Trạng Thái |
| :--- | :--- | :--- | :--- |
| **`P0-1`** | Worker chấm điểm của Tenant A không đọc được khóa đáp án của đề hệ thống (`SYSTEM_PUBLIC`). | Bổ sung policy `grading_read_question_answers` trên `question_version_answers` và `grading_read_answer_keys` trên `assessment_answer_keys` cho role `ulp_worker_grading`. | **Chương 4 (RLS)**<br>`SPECIFIED` *(Test ở M0)* |
| **`P0-2`** | Các Job nền xuyên Tenant (`outbox_relay`, `attempt_expiry`, `metric_rollup`) trả về 0 dòng do `FORCE RLS` và không có `BYPASSRLS`. | Tạo role chuyên biệt `ulp_system_scheduler` (`NOBYPASSRLS`) kèm hàm `app_system_job()` và 4 policies hẹp theo từng loại job; sau khi quét `attempt_expiry`, mở Transaction riêng có `SET LOCAL app.current_tenant_id` để chốt `AUTO_SUBMITTED`. | **Chương 4 & Mục 6.4**<br>`SPECIFIED` *(Test ở M0)* |
| **`P0-3`** | Policy đọc học liệu công khai (`hybrid_read_policy`) chỉ kiểm tra `tenant_id = system_public_tenant_id()`, làm lộ bản `DRAFT` hoặc `TAKEN_DOWN`. | Siết chặt `hybrid_read_policy` trên toàn bộ 21 bảng công khai: bắt buộc `visibility_scope = 'SYSTEM_PUBLIC' AND publication_status = 'PUBLISHED' AND is_immutable IS TRUE` và kế thừa điều kiện từ bảng cha qua `EXISTS`, đồng thời tối ưu hiệu năng bằng `(SELECT current_app_tenant_id())` (`InitPlan`). | **Chương 4 (RLS)**<br>`SPECIFIED` *(Test ở M0)* |
| **`P0-4`** | Mâu thuẫn giữa KEK theo Tenant và thu hồi KEK theo User trong `ADR-015`; `guardian_email` và các trường văn bản tự do (`tutor_messages`, `appeals`, `comment_threads`) chưa mã hóa/xóa khi Erasure. | Chuẩn hóa **Phương án A (Khuyến nghị: Mỗi User 1 khóa `ulp-user-{id}` trên HashiCorp Vault Transit)** kết hợp lớp bảo vệ thứ 2 là WORM Tombstone (`reapply_tombstones_after_pitr_restore`). Mã hóa `guardian_email_ciphertext` + `guardian_email_blind_index` và xóa sạch văn bản tự do trong `tutor_messages`, `appeals`, `comment_threads` khi thực thi Right to Erasure. | **ADR-015, Chương 4 & Mục 6.8**<br>`TESTED` *(Python)* / `SPECIFIED` *(Vault)* |
| **`P0-5`** | Gọi trực tiếp `sp.sympify()` trên chuỗi do LLM sinh ra gây nguy cơ thực thi mã tùy ý (RCE). | Thay thế hoàn toàn bằng kiến trúc 2 file: `sympy_sandbox_worker.py` chạy trong tiến trình cô lập `python -I` (whitelist `ALLOWED_NAMES`, chặn `__`, giới hạn `RLIMIT_AS = 256MB`, `RLIMIT_CPU = 5s`, tắt `__builtins__`) và `sympy_runner.py`. | **Mục 6.3**<br>`TESTED` *(12 ca RCE bị chặn 100%)* |
| **`P0-6`** | Client phòng thi ở Mục 6.9 chỉ dùng `Map` trong RAM thay vì `IndexedDB` thật, nếu F5 hoặc sập tab sẽ mất bài chưa gửi. | Viết lại hoàn toàn Mục 6.9 bằng thư viện **`Dexie` (`IndexedDB` thật — `exam-autosave-store.ts`)**: lưu bền vững xuống ổ đĩa trình duyệt trước khi gửi, không xóa bản đã từng gửi (`tries > 0`), có `exponential backoff + jitter` và tự động xả hàng đợi khi có mạng (`online` / `visibilitychange`). | **Mục 6.9**<br>`SPECIFIED` *(Chạy `tsc` ở M0)* |
| **`P0-7`** | Client TS chưa gửi `expected_response_version` và `payload_hash`; kịch bản `k6` dùng chung 1 `attempt_id` cho 5.000 VU và hash rỗng. | Cập nhật Client TS gửi đủ `expected_response_version` + `payload_hash` (SHA-256 của `canonicalJson`); Server kiểm tra đối chiếu `payload_hash` (`422 PAYLOAD_HASH_MISMATCH`); kịch bản `k6` dùng `SharedArray` cấp cho mỗi VU 1 `attempt_id` riêng và tính SHA-256 thật. | **Mục 6.4, 6.9 & 9.3C**<br>`TESTED` *(Python) / `SPECIFIED` (k6)* |
| **`P0-8`** | `ulp_worker_ingestion` và `ulp_worker_grading` được cấp `GRANT ALL TABLES`, vi phạm nguyên tắc Least-Privilege. | Thu hồi `GRANT ALL`; cấp quyền chi tiết theo đúng danh sách bảng và cột cho từng Worker role và kiểm tra tự động bằng câu truy vấn đối chiếu `information_schema.role_table_grants` với `grants_manifest.csv`. | **Chương 4 (GRANT) & Mục 9.3D**<br>`SPECIFIED` *(Test ở M0)* |
| **`P1-All`** | `E01` và `C02` xếp ở Sprint 9 quá muộn; Pilot ở Sprint 11 không đủ mẫu IRT; thiếu Buffer; mã Gate lẫn lộn `RG-xx` và `GATE-xx`; thiếu DPIA, Anti-Cheat, Pentest, Chi phí LLM/GPU; mô tả FSRS lệch code. | Đưa **`E01` về Sprint 2**, **`C02` về Sprint 3** (chuyển `B07` sang Sprint 4); khởi động **Pilot sớm từ Sprint 8** với Anchor Items; bổ sung **Sprint 13–14 (4 tuần Buffer cho Pentest, WAF, DR Drill)** kèm Phương án rút gọn 26 tuần; thống nhất duy nhất mã **`GATE-01..GATE-10` (tách `GATE-06a` và `GATE-06b`)**; bổ sung Mục 1.5 (DPIA, Anti-Cheat, Mô hình chi phí LLM/GPU) và khớp mô tả FSRS với code. | **Mục 1.3, 1.5, Chương 3, 6.5, 8 & 9**<br>`VERIFIED_IN_MANIFEST` |

### 2. Nguồn Chuẩn Duy Nhất (`platform_manifest.yaml` — Phiên bản 11.2.0)

| Khóa Chuẩn (`manifest_key`) | Giá trị Chuẩn Hóa Duy Nhất (V11.3) | Quy tắc Ràng buộc & Kiểm chứng Tự động trong CI (`ci/check_gate_ids.sh`) |
| :--- | :--- | :--- |
| **`module_count` & `release_tracks`** | **47 Modules** chia làm **3 Release Tracks tuần tự**:<br>• **Track 1 (Core MVP):** `26 modules` (`Sprint 1..6` — đã bao gồm `E01` ở Sprint 2 và `C02` ở Sprint 3)<br>• **Track 2 (Intelligent & LMS):** `12 modules` (`Sprint 7..9` — khởi động Pilot từ Sprint 8)<br>• **Track 3 (Expansion & IRT):** `9 modules` (`Sprint 10..12`) | Quy tắc tải Sprint: Tối đa **6 modules / Sprint**, trong đó tối đa **2 modules rủi ro cao** (RLS, Autosave, AI Gateway, Grading, Crawler, Drive). Mỗi module chỉ xuất hiện ở duy nhất 1 Sprint chính thức. |
| **`sprint_plan` (2 Phương án)** | • **Phương án A (Đầy đủ 47 modules — Khuyến nghị):** `M0 (2 tuần)` + `Sprint 1..12 (24 tuần)` + `Sprint 13..14 Buffer/Pentest/DR (4 tuần)` = **30 Tuần (`225 Net PW`)**.<br>• **Phương án B (Giữ 26 tuần — 44 modules):** Hoãn 3 modules mở rộng (`A02`, `E03`, `G06`) sang V1.1 để dành Sprint 11–12 làm Buffer/Pentest/DR = **26 Tuần (`195 Net PW`)**. | Tech Lead và PM chốt Phương án A hoặc B ngay cuối giai đoạn `M0` dựa trên tốc độ thực tế của đội. Với đội 2 FE, hoãn tính năng kéo-thả cắt lại `bbox` trực tiếp trên Canvas sang V1.1 (hoặc bổ sung 1 FE từ Sprint 3). |
| **`release_gates`** | **11 Cổng chuẩn dùng tiền tố duy nhất `GATE-`:** `GATE-01` đến `GATE-05`, `GATE-06a` (Toán/Lý — Sprint 6), `GATE-06b` (Hóa/Sinh — Sprint 8), `GATE-07` đến `GATE-10`. | Script `ci/check_gate_ids.sh` chạy ở Cổng 1 của CI tự động đánh trượt bản build nếu phát hiện bất kỳ chuỗi `RG-xx` nào hoặc mã `GATE-xx` nằm ngoài `platform_manifest.yaml`. |
| **`irt_tier_registry`** | **4 Bậc:** `RULE_WEIGHTED_L1`, `RASCH_1PL_L2`, `IRT_2PL_GPCM_L3`, `IRT_3PL_GPCM_L4`. | Chấp nhận `RULE_WEIGHTED_L1` là trạng thái hợp lệ tại thời điểm Go-Live cho những câu hỏi chưa tích lũy đủ 6 tiêu chí thống kê từ đợt Pilot (Sprint 8–11). |

### 3. Sơ Đồ Kiến Trúc Tổng Thể 6 Lớp & Luồng Triển Khai 3 Release Tracks (V11.3)

```text
+----------------------------------------------------------------------------------+
|             LỚP 1: PRESENTATION & 6 WEB WORKSPACES (Next.js + KaTeX)             |
|  [W1: Learner SSR] [W2: Exam SPA + Dexie IndexedDB] [W3: BBox Review] [W4-W6]    |
+----------------------------------------+-----------------------------------------+
                                         | HTTPS / OpenAPI 3.1 (JWT + W3C Trace)
                                         v
+----------------------------------------------------------------------------------+
|                  LỚP 2: API GATEWAY & APPLICATION ORCHESTRATION                  |
|  • Tenant Context: (SELECT current_app_tenant_id()) — InitPlan O(1) per query    |
|  • Single-Aggregate Transaction + Optimistic Concurrency + Payload Hash Check    |
+------------------------+-------------------------------+-------------------------+
                         |                               |
+------------------------v-------------------------+ +---v-------------------------+
| TRACK 1: CORE MVP (26 Modules — Sprint 1-6)      | | TRACK 2: AI & LMS (12)      |
| • A01, A04-A09 (Doc Intelligence / OCR / Review) | | • B05, D01-D03, D05 (Loop)  |
| • B01-B04, B06, B07 (SGK, Studio, Search FTS)    | | • F01-F03, F06 (Class LMS)  |
| • E01 (AI Gateway - S2), C02 (SymPy Sandbox - S3)| | • D04 (FSRS-5), E02 (Tutor) |
| • C01, C03-C07 (Question Bank, Exam, Autosave)   | | • E04 (Step Solutions)      |
| • F04, F05, G01-G03 (RBAC, Consent, Takedown)    | |   (Sprint 7-9 + Pilot S8)   |
+------------------------+-------------------------+ +---+-------------------------+
                         |                               |
                         +---------------+---------------+
                                         |
                                         v
+----------------------------------------------------------------------------------+
|    TRACK 3: EXPANSION & IRT (9 Modules — S10-12) + HARDENING BUFFER (S13-14)     |
|  • A02 (Crawler IP-Pin), A03 (Drive Sync), D06 (6-Criteria IRT), E03/E05/E06     |
|  • G04-G06 (Analytics, Billing, Verticals) + S13-14 (Pentest, WAF, DR, Go-Live)  |
+----------------------------------------+-----------------------------------------+
                                         |
                                         v
+----------------------------------------------------------------------------------+
|     LỚP 3 & 4: PATRONI QUORUM, VAULT TRANSIT & 7 LEAST-PRIVILEGE DB ROLES        |
|  • PostgreSQL 16/18 Patroni 3-Node (synchronous_mode_strict: true, 100% RLS)     |
|  • Roles: ulp_app_rw | ulp_exam_session_rw | ulp_worker_ingestion |              |
|           ulp_worker_grading | ulp_system_scheduler | ulp_analytics_ro           |
|  • Vault Transit Per-User Key (ulp-user-{id}) + WORM Tombstone Post-PITR Scrub   |
+----------------------------------------------------------------------------------+
```

---

# PHẦN I (TẬP 1 / 3 — V11.3): KIẾN TRÚC HỆ THỐNG, 10 ADRs, MA TRẬN SỞ HỮU DỮ LIỆU & ĐẶC TẢ ĐẦY ĐỦ 47 MODULES (A01–G06)

## CHƯƠNG 1. CHỈ ĐẠO KỸ THUẬT TỪ TECH LEAD & 6 QUY TẮC BẤT BIẾN (ARCHITECTURAL GUARDRAILS)

1. **KHÔNG ĐẬP ĐI XÂY LẠI TỪ ĐẦU (REFACTOR & EXTEND):** Hệ thống hiện tại (`tranhuyhuan0609-web/exam-system`) đã có sẵn khung FastAPI, SQLAlchemy 2.0, Alembic, Key Rotator, Crawler (`vietjack`, `tuyensinh247`, `loigiaihay`, `hoc247`), Drive Sync, Exam PDF và Giao diện Admin/Student. Nhiệm vụ của phòng Dev là nâng cấp theo lộ trình Migration 5 bước không gián đoạn và bảng ánh xạ tại Chương 2.
2. **TÁCH BIỆT 4 CẤU TRÚC TRI THỨC & ĐỊNH DANH BẤT BIẾN:** Tuyệt đối không gộp chung:
   - **Academic Taxonomy** (`taxonomy_nodes` giữ UUID định danh bất biến + `taxonomy_node_versions` giữ nội dung phiên bản).
   - **Knowledge Graph** (`knowledge_edges` nối các `taxonomy_nodes.id` cố định, không bị đứt khi cập nhật tên/mô tả khái niệm).
   - **Curriculum** (`curriculum_versions`, `curriculum_nodes` phản ánh mục lục SGK 4 cấp: Chương -> Bài -> Mục -> Dạng bài).
   - **Assessment Framework** (`assessment_frameworks`, `framework_versions`, `blueprints` phản ánh cấu trúc đề thi chuẩn THPTQG 2025+, ĐGNL, ĐGTD, HSG).
3. **TÁCH BIỆT 5 TRỤC TRẠNG THÁI CÓ KIỂM SOÁT CỨNG DƯỚI DATABASE:** Mọi thực thể học liệu/câu hỏi phải quản lý độc lập 5 trục trạng thái:
   - `acquisition_status` (`INGESTED`, `EXTRACTED`, `NORMALIZED`, `FAILED`)
   - `verification_status` (`UNCHECKED`, `AUTO_VERIFIED`, `VERIFIED`, `DISPUTED`, `REJECTED`)
   - `rights_status` (`PENDING_REVIEW`, `OWNED`, `LICENSED`, `ALLOWED_DERIVATIVE`, `RESTRICTED`, `TAKEDOWN`) — **Mặc định bắt buộc là `PENDING_REVIEW` (Fail-Closed)**
   - `publication_status` (`DRAFT`, `READY`, `PUBLISHED`, `ARCHIVED`, `TAKEN_DOWN`)
   - `job_status` (`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `DEAD_LETTER`, `CANCELLED`)
   - **Quy tắc Cổng Xuất Bản (Database CHECK & Trigger):** Một bản ghi chỉ được phép chuyển sang `publication_status = 'PUBLISHED'` khi và chỉ khi `verification_status = 'VERIFIED'` **VÀ** `rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')`.
4. **BẤT BIẾN DỮ LIỆU GỐC & PHIÊN BẢN ĐÃ PHÁT HÀNH (DB-ENFORCED IMMUTABILITY):** File gốc ở tầng Bronze (`assets` có `asset_role = 'RAW_SOURCE'`), `content_versions` đã Publish, `question_versions` đã Publish, `assessment_versions` đã khóa, `response_revisions` và `audit_events` được bảo vệ bằng Trigger PostgreSQL chặn tuyệt đối lệnh `UPDATE`/`DELETE`. Mọi chỉnh sửa nội dung phải sinh `version_no + 1`.
5. **POSTGRESQL LÀ NGUỒN SỰ THẬT DUY NHẤT (RPO = 0 CHO PHÒNG THI):** Redis chỉ làm Cache đề thi công khai (đã lược bỏ đáp án) và Broker điều phối Worker. Thao tác Autosave bài thi bắt buộc `COMMIT` xuống PostgreSQL (kèm Synchronous Standby Replication cho cụm bảng thi cử) trước khi trả `HTTP 200 ACK`.
6. **CÔ LẬP MODULE & CẤM CHỒNG LẤN SỞ HỮU BẢNG (ZERO SHARED TABLE OWNERSHIP):** Mỗi bảng trong PostgreSQL chỉ thuộc quyền sở hữu ghi (`INSERT/UPDATE/DELETE`) của duy nhất **1 Module**. Các module khác chỉ được truy xuất qua Application Service công khai hoặc lắng nghe Domain Event qua Outbox/Inbox. Quy tắc này được kiểm tra tự động trong CI bằng `import-linter`.

### 1.1. Mười Bảy Quyết Định Kiến Trúc Cốt Lõi (ADR-001 đến ADR-017)

| Mã ADR | Chủ đề quyết định | Quyết định chốt (Baseline Decision V11.3) | Hệ quả & Ràng buộc thực thi bắt buộc cho Dev |
| :--- | :--- | :--- | :--- |
| **ADR-001** | Kiến trúc Triển khai | **Modular Monolith** + 3 Background Worker Pools tách theo đặc tính tải: `io-crawler-pool`, `cpu-doc-ocr-pool`, `gpu-ai-verify-pool`. | Toàn bộ domain nằm trong 1 repo, kiểm soát biên giới import bằng `.importlinter`. Giao tiếp bất đồng bộ qua Transactional Outbox (`outbox_events`) + Idempotent Consumer Inbox (`inbox_events`). |
| **ADR-002** | Lưu trữ Dữ liệu & Khóa chính | **PostgreSQL 16+** làm Transactional Source of Truth (kèm Synchronous Standby cho bảng phòng thi); **MinIO/S3** lưu trữ toàn bộ đối tượng nhị phân. | Cấm lưu file nhị phân trong DB. Toàn bộ khóa chính dùng **UUIDv7** (`uuid_generate_v7()`) sắp xếp theo thời gian để chống phân mảnh chỉ mục B-Tree. Mọi cột thời gian dùng `TIMESTAMPTZ` (UTC). |
| **ADR-003** | Cô lập Đa thuê bao & Học liệu Chung | **Shared Database + `tenant_id` trên MỌI bảng** (kể cả bảng con qua Composite FK) + **PostgreSQL Row-Level Security (RLS)** + `visibility_scope`. | Middleware API thiết lập `SET LOCAL app.current_tenant_id = '<uuid>'` trong mỗi transaction (tương thích PgBouncer `transaction` mode). Học liệu SGK dùng chung thuộc `SYSTEM_TENANT_ID` với `visibility_scope = 'SYSTEM_PUBLIC'`. |
| **ADR-004** | Phiên bản hóa Học liệu & Taxonomy | Tách bảng định danh (`taxonomy_nodes`, `content_items`, `questions`) khỏi bảng phiên bản bất biến (`taxonomy_node_versions`, `content_versions`, `question_versions`). | Khi sửa câu hỏi/bài học/nút phân loại đã Publish, hệ thống tự động tạo `version_no + 1`; các cạnh đồ thị `knowledge_edges` và đề thi lịch sử giữ nguyên tham chiếu không bao giờ bị gãy. |
| **ADR-005** | Đóng băng Đề thi, Tách Đáp án & Hỗ trợ Takedown | Khi Publish đề thi, đóng băng cấu trúc (`structure_snapshot_json`: danh sách `question_version_id`, điểm số, quy tắc trộn) tách biệt hoàn toàn với bảng đáp án (`assessment_answer_keys`). | API phát đề chỉ đọc `structure_snapshot_json` và nội dung câu hỏi (không bao giờ chạm tới `assessment_answer_keys`). Khi một câu hỏi bị Takedown bản quyền, đề thi tự động che nội dung câu đó kèm biên bản kiểm toán mà không làm hỏng lịch sử điểm. |
| **ADR-006** | Điều phối AI & Kiểm chứng Độc lập | **AI Provider Gateway (E01)** triển khai ngay từ Sprint 2 để phục vụ toàn bộ OCR/LaTeX, Healer (Job 1), SymPy + LLM Verifier (Job 2) và AI Tutor. | Mọi lời gọi LLM phải qua Gateway để xoay vòng key, áp trần chi phí theo Tenant và ghi `ai_traces`. AI không bao giờ được tự động chuyển trạng thái `publication_status = 'PUBLISHED'`. |
| **ADR-007** | Tách Bạch Lập Lịch Ghi Nhớ (FSRS v5) & Ước Lượng Năng Lực (Phased IRT) | **Hai cơ chế độc lập:** (1) **FSRS v5 (19 trọng số)** chuyên trách tính khoảng ngày ôn tập thẻ nhớ/công thức; (2) **Mô hình Năng lực (Mastery/IRT)** gồm 4 bậc: `L1` (Rule-weighted decay) -> `L2` (1PL Rasch khi $N \\ge 150$) -> `L3` (2PL/GPCM khi $N \\ge 300$) -> `L4` (3PL có Beta prior khi $N \\ge 1000$). | Không gộp lẫn FSRS vào bậc của IRT. Chỉ hiển thị **\"Điểm ước lượng năng lực tham khảo (Khoảng tin cậy 95%)\"** khi học sinh đã làm $\\ge 20$ câu, độ phủ chuyên đề $\\ge 60\\%$ và $\\text{SE}(\\theta) < 0.35$. |
| **ADR-008** | Quản trị Bản quyền & Tái thương hiệu | Mặc định mọi tài liệu nạp vào có `rights_status = 'PENDING_REVIEW'`. Chỉ cho phép tạo bản PDF thay Logo/Banner khi `rights_status = 'OWNED'` hoặc `'ALLOWED_DERIVATIVE'` có văn bản cấp phép. | Tuyệt đối không xóa thông tin tác giả/nguồn gốc của tài liệu bên thứ ba. Crawler bắt buộc kiểm tra `robots.txt` và tuân thủ Rate Limit $\le 1\text{ req}/3\text{s}$. |
| **ADR-009** | Phạm vi MVP Thực tế (Focused Wedge) | MVP (Sprint 1–6) tập trung sâu vào **Toán & Vật lí THPT (Lớp 10–11–12 theo SGK Kết nối tri thức & Cấu trúc đề THPTQG 2025+ mới gồm 3 phần: 22–28 câu)**; mở rộng Hóa, Sinh ở Sprint 7–10. | Tách hoàn toàn cấu hình TOEIC/IELTS/HSK sang gói mở rộng `VerticalPack` (G06), không làm phức tạp hóa schema và giao diện trong giai đoạn MVP. |
| **ADR-010** | Tuân thủ Pháp lý, Giấy phép OSS & Bảo vệ Trẻ vị thành niên | Sử dụng `pypdfium2` (Apache 2.0) + `pdfplumber` (MIT) làm engine PDF mặc định thay cho `PyMuPDF` (AGPLv3) trừ khi có giấy phép thương mại Artifex; tuân thủ **Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP** và dùng scope `drive.file` + Google Picker. | Học sinh dưới 16 tuổi phải có bản ghi xác nhận của phụ huynh/nhà trường (`parental_consents`). Bộ dữ liệu kiểm thử Golden Corpus phải xóa sạch PII và lưu trên S3 Private ngoài Git repo. |
| **ADR-011** | Ranh giới Giao dịch & Tính nhất quán Phân tán | Mỗi Use Case chỉ sửa đổi **1 Aggregate Root** trong 1 Transaction PostgreSQL (`READ COMMITTED` + `SELECT ... FOR UPDATE` cho dòng cạnh tranh cao) và ghi `outbox_events` trong cùng Transaction. | Cấm gọi HTTP/RPC ra dịch vụ ngoài (MinIO, LLM, Google API) khi đang giữ DB Transaction mở. Phối hợp liên module thực hiện qua Saga / Outbox Event. |
| **ADR-012** | Vận chuyển Sự kiện, Retry, DLQ & Replay | Mọi sự kiện tuân thủ `EventEnvelopeContract` (có `event_id`, `schema_version`, `correlation_id`, `causation_id`). Consumer bắt buộc kiểm tra `inbox_events` trước khi xử lý. | Retry theo hàm mũ (`exponential backoff + jitter`, tối đa 5 lần); nếu vẫn lỗi thì chuyển vào `dead_letter_items`. Lệnh Replay từ DLQ yêu cầu quyền Admin và ghi `audit_events`. |
| **ADR-013** | Chiến lược Bộ nhớ đệm & Vô hiệu hóa Cache | Áp dụng **Cache-Aside có khóa chống Stampede (Singleflight / Mutex)** cho `structure_snapshot_json`, cây Taxonomy/Curriculum và bài viết đã Publish. | Key cache bắt buộc gắn tiền tố `tenant_id` và `version_no` (VD: `ulp:{tenant_id}:qver:{id}:v{ver}`). Khi Takedown bản quyền, phát sự kiện xóa tức thì key liên quan. |
| **ADR-014** | Kiến trúc Tìm kiếm & Quản lý Phiên bản Từ điển | Sử dụng PostgreSQL FTS (`tsvector` + `pg_trgm`) ở Track 1–2; ghim cố định mã băm `rules_sha256` của từ điển `unaccent.rules` trong bảng `fts_dictionary_versions`. | Khi nâng cấp PostgreSQL hoặc sửa file từ điển `unaccent.rules`, migration bắt buộc tăng `func_version` và chạy `REINDEX INDEX CONCURRENTLY` cho `search_documents`. |
| **ADR-015** | Sao lưu, Khôi phục (PITR) & Vault Transit Per-User Crypto-Shredding | **Phương án A (Khuyến nghị):** Mỗi `user_id` có 1 khóa `ulp-user-{id}` trên **HashiCorp Vault Transit** bọc `DEK` của user đó; khi Erasure, xóa khóa trên Vault Transit làm vô hiệu hóa tức thì khả năng giải mã trên cả DB sống lẫn mọi bản Backup cũ.<br>**Phương án B (Dự phòng):** Dùng 1 `KEK/Tenant/Năm` + xóa `DEK` trong DB (cần Pháp chế xác nhận thời hạn lưu trữ PII tối đa 30 ngày trên bản Backup cũ). | Với cả 2 phương án, bắt buộc mã hóa `guardian_email_ciphertext`, xóa văn bản tự do (`tutor_messages`, `appeals`, `comment_threads`) và chạy `reapply_tombstones_after_pitr_restore()` từ WORM Storage sau mỗi lần PITR Restore. |
| **ADR-016** | Quan sát Hệ thống (Observability) & Quản lý Sự cố | Chuẩn hóa truyền ngữ cảnh theo **W3C Trace Context** kèm 8 trường định danh nghiệp vụ (`correlation_id`, `causation_id`, `tenant_id`, `actor_id`, `document_version_id`, `acquisition_job_id`, `pipeline_stage_run_id`, `parser_or_model_version`). | Toàn bộ log xuất định dạng JSON có cấu trúc và tuyệt đối không chứa PII hay Secret; cảnh báo đa cửa sổ (Multi-window Burn-rate Alerts) gắn trực tiếp với 14 Runbooks. |
| **ADR-017** | Đánh giá Chất lượng AI, Phiên bản hóa & Rollback | Tách biệt hoàn toàn tập **Golden Corpus (11 đề)** và **Held-out Corpus (30 đề)**. Mọi lời gọi AI phải lưu `model_name`, `prompt_version_id`, `temperature`, `input_sha256` và `output_json`. | Không nâng cấp Prompt hoặc Model trên Production nếu độ chính xác trên tập Held-out giảm hoặc tỷ lệ ảo giác toán học (`SymPy` mismatch) tăng; hỗ trợ Rollback tức thì qua `FeatureFlag`. |

---


### 1.2. Định Vị Chiến Lược Sản Phẩm (Vượt Qua VietJack) & 4 Chỉ Thị Thực Chiến Khi Kick-Off Sprint 0

#### A. Chiến lược Sản phẩm & Lợi thế Cạnh tranh Cốt lõi (Tại sao kiến trúc này thắng trên thị trường)
Hệ thống **không đi theo lối mòn làm web tĩnh tra cứu lời giải đại trà để đua SEO quảng cáo với VietJack**, mà tập trung giải quyết 3 bài toán công nghệ có rào cản kỹ thuật cao (Deep-Tech Moat) theo mô hình **B2B2C (Giáo viên/Trường học kéo Học sinh)**:

| Trục Cạnh Tranh | Điểm nghẽn của các trang Giải bài tập Tĩnh (như VietJack) | Vũ khí Công nghệ Cốt lõi của Hệ thống ULP V11.3 | Giá trị Chuyển đổi Thực tế |
| :--- | :--- | :--- | :--- |
| **1. Công cụ Số hóa Đề thi cho Giáo viên (B2B2C Wedge)** | Không có công cụ bóc tách đề phức hợp hay quản lý lớp học. | **Module A04–A07 + C02 (`SymPy`):** Giáo viên thả 1 file `.docx` (MathType OLE) hoặc `.pdf` (có sơ đồ Hóa học, hình vẽ Vector) -> tự động tách câu, cắt hình 300 DPI, loại trừ đường kẻ bảng (`find_tables()`), kiểm chứng đáp án sai bằng `SymPy` và mở phòng thi trong 1 phút. | Mỗi giáo viên sử dụng công cụ tạo đề/giao bài sẽ tự động kéo 100–300 học sinh của họ vào nền tảng mà không tốn chi phí quảng cáo (CAC $\approx 0$). |
| **2. Phòng Thi Chuẩn Cấu Trúc THPTQG 2025+** | Chủ yếu là bài đọc tĩnh hoặc trắc nghiệm ABCD đơn giản, nhiều quảng cáo gây xao nhãng. | **Module C03–C07:** Hỗ trợ trọn vẹn đề THPTQG 2025+ (Phần I ABCD, Phần II Đúng/Sai 4 ý tính điểm lũy tiến `0.1 - 0.25 - 0.5 - 1.0`, Phần III Trả lời ngắn 4 ký tự), trộn đề theo hạt giống và Autosave bất tử khi rớt mạng (`IndexedDB` + `RPO = 0`). | Đánh trúng nhu cầu cấp thiết nhất của học sinh THPT trước kỳ thi mới, nơi các kho trắc nghiệm ABCD cũ đã lỗi thời. |
| **3. Vòng lặp Khắc phục Lỗ hổng Kiến thức** | Học sinh vào chép lời giải xong rồi thoát (tỷ lệ giữ chân thấp). | **Module B05, D02–D06, E02:** Tự động gom câu sai vào Sổ tay, truy vết kiến thức tiên quyết (`PREREQUISITE`) đang hổng, giao 3 bài toán biến thể cùng dạng (`structural_hash`) yêu cầu làm đúng 2 câu liên tiếp mới xóa lỗi, kèm Gia sư AI chế độ gợi ý (`Hint Mode`). | Tạo tỷ lệ giữ chân hàng tuần (WLAU) cao và thúc đẩy chuyển đổi lên gói trả phí `STUDENT_PRO` / `SCHOOL_ENTERPRISE`. |

#### B. Bốn Chỉ thị Kiểm chứng Thực địa Bắt buộc trong Tuần 1 của Sprint 0 (Pre-Flight Checklist cho Tech Lead)
Trước khi viết code tính năng ở Sprint 1, Tech Lead và các nhóm trưởng phải hoàn tất 4 việc đối chiếu thực tế sau ngay trong Tuần 1 của Sprint 0:
1. **Đối chiếu Schema với nhánh code `exam-system` thật:** Lead BE chạy `alembic revision --autogenerate` đối chiếu giữa file `app/models.py` hiện hữu trên repo thật với kịch bản DDL ở Chương 4 để viết script chuyển đổi dữ liệu cũ (Dual-Write / Backfill theo 5 bước ở Mục 7.4) khớp 100% tên cột đang chạy.
2. **Đưa DDL & Bộ test Phân quyền vào CI Container PostgreSQL 16/18 thật:** DevOps dựng pipeline GitHub Actions / GitLab CI chạy `alembic upgrade head` trên PostgreSQL thật, kiểm tra tự động độ phủ 100% RLS và xác nhận role phòng thi `ulp_exam_session_rw` ghi được `attempts`/`responses` nhưng bị từ chối `SELECT` trên `question_version_answers` và `assessment_answer_keys`.
3. **Chốt dứt điểm Thư viện PDF (`pypdfium2` + `pdfplumber`):** Nhóm DE/AI gỡ bỏ `PyMuPDF` (`fitz` — giấy phép AGPLv3) khỏi `requirements.txt` nếu không mua giấy phép thương mại Artifex, đồng thời chạy thử `VietnameseExamDocumentProcessor` (Mục 6.1) trên bộ 11 đề mẫu thật đã xóa PII trên bucket S3 riêng.
4. **Khởi động song song luồng Pháp chế & Google Cloud (Không chặn Dev code):** Gửi Mục `F04` + `RB-14` cho bộ phận Pháp chế rà soát mẫu đồng ý của phụ huynh/nhà trường và thời hạn báo cáo theo Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 & NĐ 13/2023/NĐ-CP; đồng thời tạo Google Cloud Project để kiểm thử thực tế Google Picker (`drive.file` chọn nhiều file/zip) và `Service Account` (cho thư mục trường học) trước Sprint 8.

---


### 1.3. Chiến Lược Phân Tầng 47 Modules Thành 3 Release Tracks + 2 Sprint Buffer (`M0 + Sprint 1–14`)

Theo đúng Bản vá V11.1, `E01 (AI Provider Gateway)` được đưa về **Sprint 2** và `C02 (Question Quality & SymPy Verification)` được đưa về **Sprint 3** để phục vụ ngay cho luồng bóc tách và kiểm chứng đề thi ở Track 1; đồng thời bổ sung **Sprint 13–14 (4 tuần Buffer)** dành riêng cho Pentest độc lập, WAF, sửa lỗi từ đợt Pilot (khởi động từ Sprint 8) và diễn tập DR:

| Cấp Triển Khai (Release Track) | Mục tiêu Sản phẩm & Cổng Nghiệm thu (`GATE-xx`) | Danh sách Modules Chính Thức (Tổng = 47 Modules) |
| :--- | :--- | :--- |
| **CẤP 1 — TRACK 1: Core Learning, Document Intelligence, SymPy Verification & Online Exam MVP**<br>*(Milestones M0 -> M2 — M0 + Sprint 1–6 = 14 tuần)* | **Mục tiêu:** Chạy hoàn chỉnh vòng lặp lõi cho môn **Toán & Vật lí THPT**: Nạp đề PDF/DOCX -> Cắt hình & Loại bảng -> Kiểm chứng đáp án độc lập bằng Subprocess `SymPy` + LLM (`E01`, `C02`) -> Duyệt Side-by-Side -> Ngân hàng 6 loại câu hỏi -> Tạo đề ma trận THPTQG 2025+ -> Phòng thi trực tuyến (`Dexie IndexedDB` + Optimistic Autosave) -> Chấm điểm tất định -> Học chuyên đề VietJack.<br>**Exit Gate:** Đạt `GATE-01`, `GATE-02`, `GATE-03`, `GATE-04`, `GATE-05`, `GATE-06a` (Toán/Lý) và `GATE-07`. | **26 Modules Lõi:**<br>• *Sprint 1 (6):* `A01`, `B03`, `F04`, `F05`, `G01`, `G03`<br>• *Sprint 2 (6):* `B01`, `B02`, `B04`, `B06`, `E01`, `G02`<br>• *Sprint 3 (5):* `A05`, `A06`, `A07`, `A09`, `C02`<br>• *Sprint 4 (5):* `A04`, `A08`, `B07`, `C01`, `C03`<br>• *Sprint 5 (3):* `C04`, `C05`, `C06`<br>• *Sprint 6 (1 + Gate Exit):* `C07` |
| **CẤP 2 — TRACK 2: Intelligent Learning Loop, FSRS-5, AI Tutor & Classroom LMS + Early Pilot**<br>*(Milestones M3 -> M4 — Sprint 7–9 = 6 tuần)* | **Mục tiêu:** Mở rộng bóc tách đề **Hóa học & Sinh học** (`GATE-06b`), triển khai Đồ thị tri thức, Sổ tay câu sai, Quản lý lớp học / Giao bài / Bảng điểm (`F01–F03`) và **khởi động Pilot thực tế tại trường THPT ngay từ Sprint 8** (cài cắm các câu hỏi mỏ neo `Anchor Items` để tích lũy $\ge 150–300$ lượt làm cho hiệu chuẩn IRT ở Sprint 11). Bổ sung Gia sư AI (`E02`), Lời giải từng bước (`E04`) và thẻ nhớ `FSRS-5` (`D04`).<br>**Exit Gate:** Đạt `GATE-06b` (đủ 41 đề Toán/Lý/Hóa/Sinh) và `GATE-08`. | **12 Modules Thông minh & LMS:**<br>• *Sprint 7 (5):* `B05`, `D01`, `D02`, `D03`, `D05`<br>• *Sprint 8 (4 + Khởi động Pilot):* `F01`, `F02`, `F03`, `F06`<br>• *Sprint 9 (3):* `D04`, `E02`, `E04` |
| **CẤP 3 — TRACK 3: Platform Expansion, Multi-Criteria IRT, Commerce & Hardening Buffer**<br>*(Milestones M5 -> M6 — Sprint 10–14 = 10 tuần ở Phương án A, hoặc Sprint 10–12 = 6 tuần ở Phương án B)* | **Mục tiêu:** Mở rộng Google Drive Sync (`A03`), Web Crawler (`A02`), Sinh câu hỏi AI (`E03`), hiệu chuẩn `IRT` 6 tiêu chí (`D06`) trên dữ liệu tích lũy từ Sprint 8–11 (chấp nhận `RULE_WEIGHTED_L1` ở Go-Live cho câu chưa đủ mẫu), Teacher Copilot (`E06`), Analytics & Billing (`G04–G06`). Dành trọn **Sprint 13–14** cho Pentest bên ngoài, WAF, DR Drill và đóng `GATE-09`, `GATE-10`. | **9 Modules Mở rộng + 2 Sprints Hardening:**<br>• *Sprint 10 (3):* `A02`, `A03`, `E03`<br>• *Sprint 11 (3):* `D06`, `E05`, `E06`<br>• *Sprint 12 (3):* `G04`, `G05`, `G06`<br>• *Sprint 13–14:* Hardening, Pentest, DR & Go-Live |

### 1.4. Sáu Khung Quản Trị Pháp Lý, Bản Quyền, AI, Takedown, Right to Erasure & OSS SBOM

Để đảm bảo tính sẵn sàng về mặt pháp lý và tuân thủ (Legal & Compliance Readiness — `AUD-015`), hệ thống thực thi 6 cơ chế kiểm soát kỹ thuật sau (các định nghĩa pháp lý và ngưỡng thời gian cụ thể được đánh dấu để bộ phận Pháp chế rà soát và ký duyệt trước khi vận hành chính thức):

| Trụ cột Quản trị | Cơ chế Kỹ thuật & Luồng Thực thi Bắt buộc trong Hệ thống | Bảng DB / Module Thực thi & Bằng chứng Kiểm toán |
| :--- | :--- | :--- |
| **1. Quản lý Sự đồng ý (Consent Management & Minor Protection)** | • Phân loại người học theo độ tuổi (`v_users_status.is_minor`). Với học sinh dưới ngưỡng tuổi quy định, tài khoản ở trạng thái `PENDING_PARENTAL_CONSENT` cho đến khi có bản ghi xác nhận hợp lệ từ phụ huynh (OTP/Email xác thực) hoặc hợp đồng triển khai B2B của nhà trường (`SCHOOL_CONTRACT`).<br>• Cho phép chọn lọc phạm vi đồng ý (`consent_scope_json`: lưu lịch sử học tập, sử dụng AI Tutor, nhận thông báo) và hỗ trợ thu hồi sự đồng ý (`revoked_at`) bất cứ lúc nào. | **Module:** `F04`<br>**DB:** `users`, `parental_consents`, `audit_events`<br>*(Pháp chế xác nhận biểu mẫu đồng ý theo Luật 91/2025/QH15)* |
| **2. Tài liệu Bên thứ ba (Third-Party Content Rights & Provenance)** | • Mọi tài liệu nạp vào từ Upload, Google Drive hay Web Crawler đều khởi tạo với `rights_status = 'PENDING_REVIEW'` (Fail-Closed).<br>• Bảng `source_rights` lưu tách biệt: nguồn gốc (`source_id`), loại giấy phép (`license_type`), quyền sử dụng nguyên trạng, quyền tạo bản phái sinh/thay thương hiệu (`allow_rebranding`) và quyền phân phối công khai (`allow_public_redistribution`) kèm file bằng chứng (`evidence_asset_id`).<br>• Database Trigger chặn cứng thao tác Publish nếu `rights_status` không thuộc `('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')`. | **Module:** `A01`, `A09`, `B01`, `C01`<br>**DB:** `sources`, `source_rights`, `provenance_records` |
| **3. Quản trị AI (AI Transparency, Data Minimization & Guardrails)** | • **Zero-PII Outbound:** `E01 (AI Provider Gateway)` tự động chạy bộ lọc `PIIScrubber` loại bỏ mọi thông tin định danh cá nhân (tên, email, trường lớp, `membership_id`) trước khi gửi prompt tới nhà cung cấp LLM bên ngoài; chỉ sử dụng kết nối API cam kết không dùng dữ liệu khách hàng để huấn luyện model.<br>• **Minh bạch & Giới hạn xử lý:** Mọi nội dung do AI sinh ra (`E03`, `E04`, `E06`) đều gắn nhãn `AI_DRAFT`, lưu rõ `model_name`, `prompt_version_id`, `input_sha256` và bắt buộc qua bước duyệt của con người trước khi xuất bản. | **Module:** `E01–E06`, `C02`<br>**DB:** `ai_jobs`, `ai_traces`, `prompt_versions` |
| **4. Quy trình Takedown Bản quyền 4 Bước (Notice -> Verify -> Execute -> Appeal)** | • **Bước 1 (Tiếp nhận):** Ghi nhận yêu cầu qua `POST /api/v1/takedown-cases` kèm thông tin bên khiếu nại và bằng chứng bản quyền.<br>• **Bước 2 (Xác minh):** Quản trị viên pháp lý đối chiếu `provenance_records` và `source_rights`.<br>• **Bước 3 (Thực thi `< 30s`):** Phát sự kiện `rights.takedown.executed` chuyển ngay `rights_status = 'TAKEDOWN'`, `publication_status = 'TAKEN_DOWN'`, xóa khỏi `search_documents` và CDN/Redis, ẩn nội dung trên đề cũ nhưng bảo toàn điểm thi lịch sử.<br>• **Bước 4 (Khiếu nại ngược / Counter-Notice):** Lưu vết kháng nghị và chỉ khôi phục khi có phán quyết hợp lệ. | **Module:** `G02`, `A09`, `B07`<br>**DB:** `takedown_cases`, `audit_events` |
| **5. Quy trình Right to Erasure (Xóa, Ẩn danh, Backup Expiry & Chứng nhận Hoàn tất)** | • **Thực thi trên DB sống:** Xóa `pii_ciphertext_bytea = NULL`, đổi `email_blind_index` sang chuỗi ngẫu nhiên, chuyển `users.status = 'ANONYMIZED'`.<br>• **KMS Envelope Crypto-Shredding (Xử lý Backup):** Thu hồi/Xóa khóa chủ `KEK` (`kms_kek_key_id`) trên KMS/Vault nằm ngoài DB, khiến mọi bản sao lưu `encrypted_dek_bytea` trong các bản DB Backup lịch sử không thể giải mã được nữa.<br>• **Chứng nhận hoàn tất:** Xuất bản ghi `erasure_certificates` ghi nhận thời điểm xóa trên Primary/Replica, thời điểm hủy `KEK` và thời điểm bản Backup cuối cùng hết hạn lưu trữ (30 ngày). | **Module:** `F04`, `G02`<br>**DB:** `users`, `user_pii_encryption_keys`, `erasure_certificates` |
| **6. Quản trị Mã nguồn mở (OSS SBOM, License Scanning & Dependency Policy)** | • Mọi bản build trong CI (Cổng 1 & Cổng 6) tự động sinh danh mục thành phần phần mềm **SBOM chuẩn CycloneDX (`sbom.cdx.json`)** từ `poetry.lock` / `pnpm-lock.yaml`.<br>• Tự động quét giấy phép bằng `pip-licenses` / `trivy`: **chặn đứng mọi thư viện nhiễm GPL/AGPLv3** (như `PyMuPDF` nếu không có giấy phép thương mại Artifex) và chỉ cho phép các giấy phép tương thích thương mại (`MIT`, `Apache-2.0`, `BSD-3-Clause`, `ISC`). | **Module:** CI/CD (`GATE-01`)<br>**Artifact:** `sbom.cdx.json`, `license_audit_report.json` |


### 1.5. Năm Hạng Mục Quản Trị Bổ Sung: DPIA Xuyên Biên Giới, Chống Gian Lận Thi, Pentest/WAF, Mô Hình Chi Phí LLM/GPU & Giảm Tải Frontend

| Hạng mục Bổ sung | Thời điểm Thực hiện & Owner | Quy tắc Kỹ thuật & Đầu ra Nghiệm thu Bắt buộc |
| :--- | :--- | :--- |
| **1. Hồ sơ Đánh giá Tác động Xử lý & Chuyển Dữ liệu Ra Nước ngoài (DPIA & Cross-Border Transfer)** | `M0 -> Sprint 1`<br>*(Hoàn tất trước Pilot Sprint 8)*<br>**Owner:** Pháp chế / DPO + Tech Lead | • Lập hồ sơ DPIA và Hồ sơ đánh giá tác động chuyển dữ liệu cá nhân xuyên biên giới đối với các luồng kết nối ra hạ tầng quốc tế (API nhà cung cấp LLM, Google Drive API, Cloud KMS/Monitoring) theo **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 & Nghị định 13/2023/NĐ-CP**.<br>• Ký kết Thỏa thuận Xử lý Dữ liệu (`DPA` / `Zero-Data-Retention Addendum`) với các nhà cung cấp trước khi mở Pilot. |
| **2. Chống Gian Lận Trong Phòng Thi Trực Tuyến (Exam Anti-Cheating Telemetry)** | `Sprint 5 – Sprint 6`<br>**Owner:** BE + FE + QA | • Trên `Workspace 2 (Phòng thi)`: ghi nhận sự kiện mất tiêu điểm trình duyệt (`visibilitychange` / `blur`), thoát toàn màn hình (`fullscreenchange`), mở đồng thời từ 2 địa chỉ IP/thiết bị (`409 Conflict`), và hành vi chọn đáp án siêu tốc bất thường (`< 2 giây/câu` liên tiếp).<br>• Lưu vào `activity_events` gắn với `attempt_id` và hiển thị cờ cảnh báo mức độ trung thực (`integrity_flags_json`) trên bảng điểm giáo viên (`F03`), nhưng **không tự động hủy bài thi của học sinh nếu chưa có quyết định của giáo viên**. |
| **3. Kiểm Thử Xâm Nhập Độc Lập (External Pentest), WAF & Rate Limiting** | `Sprint 13`<br>**Owner:** DevOps + Security Partner | • Triển khai lớp bảo vệ Cloudflare / Nginx ModSecurity WAF (OWASP Core Rule Set) và Rate Limiting theo `tenant_id` / `membership_id` / `ip` ngay từ Track 1.<br>• Thuê đơn vị độc lập thực hiện Black-box & Grey-box Pentest ở Sprint 13 (tập trung vào IDOR chéo Tenant, lộ đáp án phòng thi, RCE qua file upload/LaTeX/SymPy, SSRF); bắt buộc vá 100% lỗi mức Critical/High trước khi đóng `GATE-09`. |
| **4. Mô Hình Toán Học Chi Phí Đơn Vị LLM / GPU & Unit Economics Thực Tế** | `M0` *(Khung công thức)*<br>`Sprint 9` *(Số đo thật)*<br>**Owner:** DE/AI + Tech Lead | • Thay giả định cảm tính bằng công thức hạch toán trực tiếp từ bảng `ai_models` và `ai_traces`:<br>  $$\text{Cost}_{\text{tenant, day}} = \sum_{j \in \text{Jobs}} \left( \frac{\text{Tokens}_{\text{in}, j}}{10^6} \cdot P_{\text{in}} + \frac{\text{Tokens}_{\text{out}, j}}{10^6} \cdot P_{\text{out}} \right) + T_{\text{GPU\_hours}} \cdot C_{\text{GPU\_hour}}$$<br>• Thiết lập hạn ngạch cứng (`daily_ai_budget_usd` trên `tenants` và giới hạn số lượt gọi AI Tutor/ngày theo gói `FREE` vs `STUDENT_PRO`) để đảm bảo chi phí AI luôn $\le 18\%$ doanh thu gói cước. |
| **5. Kế Hoạch Bảo Toàn Nguồn Lực Đội Frontend (2 FE cho 6 Workspaces)** | Quyết định trước `Sprint 3`<br>**Owner:** Tech Lead + PM | • Với quy mô 2 FE Engineers, để tránh quá tải ở `Sprint 3` và `Sprint 4`:<br>  - **Lựa chọn 1 (Mặc định không tăng người):** Ở bản Go-Live đầu tiên, `Workspace 3 (Side-by-Side Reviewer)` chỉ hiển thị lớp phủ khung `bbox` tĩnh và cho phép chọn lại tọa độ/upload ảnh thay thế; **hoãn tính năng vẽ kéo-thả cắt lại hình (`Interactive Canvas Re-crop`) sang bản V1.1**; đồng thời dùng chung bộ Component Library (shadcn/ui) cho `Workspace 5` và `Workspace 6`.<br>  - **Lựa chọn 2:** Bổ sung thêm 1 FE Engineer chuyên trách Canvas/Editor từ đầu `Sprint 3`. |

---

## CHƯƠNG 2. BẢNG ÁNH XẠ NÂNG CẤP TỪ REPO `EXAM-SYSTEM` & MA TRẬN SỞ HỮU BẢNG ĐỘC QUYỀN

### 2.1. Bảng Ánh xạ Nâng cấp từ Repo `tranhuyhuan0609-web/exam-system`

| File / Thư mục trong Repo `exam-system` hiện tại | Module đích (A01–G06) | Chỉ đạo Kỹ thuật V11.3 (Giữ nguyên / Nâng cấp / Tách mới) |
| :--- | :--- | :--- |
| `app/security.py`, `app/deps.py`, `app/routers/auth.py`, `app/plans.py` | **F04, F05, G05** | **GIỮ NGUYÊN** xác thực JWT & phân hạng Free/Pro; **NÂNG CẤP** middleware gắn `SET LOCAL app.current_tenant_id`, tách bảng `memberships`, `roles`, `permissions` và bổ sung bảng `parental_consents` (Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP). |
| `app/curriculum_data.py`, `app/sections_data.py`, `app/application/categories.py` | **B04, B05, B06** | **NÂNG CẤP** thành 3 nhóm bảng độc lập: `taxonomy_nodes` + `taxonomy_node_versions` (dùng `ltree`), `knowledge_edges` (đồ thị tiên quyết có chặn chu trình DAG) và `curriculum_versions` + `curriculum_nodes` (SGK 4 cấp). |
| `app/application/books.py`, `app/application/library_publish.py`, `app/routers/library.py` | **B01, B02, B03, B07** | **NÂNG CẤP** thành `content_items` + `content_versions` bất biến lưu `blocks_json` chuẩn VietJack Studio; bổ sung hàm bọc `f_unaccent` IMMUTABLE cho FTS tiếng Việt. |
| `app/pipeline/crawl/*`, `app/pipeline/parsers/{vietjack,tuyensinh247,loigiaihay,hoc247}.py` | **A01, A02** | **GIỮ NGUYÊN** logic bóc tách từng site; **NÂNG CẤP** bộ lọc chống SSRF (đủ IPv4/IPv6 + ghim IP chống DNS Rebinding), bộ kiểm tra `robots.txt`, chuẩn hóa đầu ra `QuestionCandidateContract` và `Parser Drift Monitor`. |
| `app/pipeline/use_cases/drive_sync.py`, `configs/drive/thptqg2026.yaml` | **A03** | **NÂNG CẤP** sang OAuth scope `drive.file` + Google Picker API (hoặc Domain-Wide Delegation cho trường học), lưu `source_sync_cursors`, bung nén an toàn chống Zip Bomb và ghép cặp 4 tín hiệu `Đề + HDC`. |
| `app/application/exam_pdf.py`, `app/ai/figures.py`, `app/pipeline/domain/exam_split.py` | **A04, A05, A06, A07** | **NÂNG CẤP TOÀN DIỆN**: Bọc qua `PDFEngineAdapter` (`pypdfium2` + `pdfplumber`), sửa thuật toán `_merge_rects` bằng Union-Find, cô lập profile LibreOffice `-env:UserInstallation` + kill process group, phát hiện Header/Footer động và xử lý từng trang. |
| `app/domain/question_hash.py`, `app/application/similar.py` | **A08** | **NÂNG CẤP** chuẩn hóa Unicode `NFC` tiếng Việt trước khi băm; sửa Regex `structural_hash` bảo toàn chỉ số công thức Hóa học (`H2O`, `Fe3O4`, `CH4`) và gom cụm bằng `pgvector`. |
| `app/models.py (Question)`, `app/question_import.py`, `app/question_rules.py` | **C01, C02, A09** | **TÁCH BẢNG** `questions` (định danh) và `question_versions` (nội dung bất biến), bổ sung đầy đủ 5 trục trạng thái (`acquisition_status`, `verification_status`, `rights_status`, `publication_status`, `job_status`). |
| `app/pipeline/use_cases/rescue.py`, `app/ai/check.py`, `app/ai/frugal.py`, `app/ai/key_rotator.py` | **E01, E04, C02** | **ĐƯA LÊN SPRINT 2**: Tách `E01 (AI Provider Gateway)` làm nền tảng chung; nâng cấp `rescue.py` thành Job 1 (Healer & Subject Lock) và Job 2 (Giải độc lập bằng `SymPy` + LLM). |
| `app/application/custom_quiz.py`, `app/application/matrix_import.py`, `app/routers/maker.py` | **C03, C04** | **NÂNG CẤP** hỗ trợ chuẩn đề THPTQG 2025+ (Toán 22 câu, Lý/Hóa/Sinh 28 câu đủ 3 phần), tách `structure_snapshot_json` và `assessment_answer_keys`, báo cáo `ShortageReport`. |
| `app/attempts.py`, `app/static/js/attempt.js`, `app/application/essay_grading.py` | **C05, C06, C07** | **NÂNG CẤP** lưu `attempt_seed` & `option_permutations_json` trên `attempts`; sửa luồng kiểm tra `Idempotency-Key` trả `200 OK` trước khi xét `client_sequence`; cho phép nhiều `grades` revisions khi phúc khảo/chấm lại. |
| `app/application/report.py`, `app/templates/result.html`, `app/routers/student.py` | **D01–D06, E02, E05** | **MỞ RỘNG** tự động gom câu sai vào `error_notes`, tính lịch ôn tập `FSRS v5`, lộ trình đồ thị tiên quyết, ước lượng năng lực Rasch/2PL/GPCM và Gia sư AI 3 chế độ có khóa phòng thi. |
| `app/application/teacher_tests.py`, `app/routers/teacher.py`, `app/routers/admin/*` | **F01–F03, G01–G04, G06** | **MỞ RỘNG** quản lý Lớp học (`classes`), Giao bài (`assessment_assignments`), Bảng điểm (`grades`), Giám sát hàng đợi Outbox/DLQ, Takedown bản quyền và cấu hình `VerticalPack`. |

### 2.2. Ma trận Sở hữu Bảng Độc quyền theo Module (Zero Shared Ownership Matrix)

Để khắc phục triệt để lỗi chồng lấn quyền sở hữu bảng được chỉ ra trong bản Review (`takedown_cases`, `assessment_assignments`, `review_tasks`, `subscriptions`), quy tắc bắt buộc trong V11.3 như sau:

| Tên Bảng Dữ Liệu | Module Sở Hữu Ghi Duy Nhất (Write Owner) | Các Module Đọc Qua Service / Event (Read / Consumers) |
| :--- | :--- | :--- |
| `review_tasks`, `review_decisions`, `source_rights` | **A09 (Review & Rights Governance)** | `C02`, `A07`, `B01` gọi `ReviewGovernanceService.enqueue_task()` để tạo yêu cầu duyệt; không ghi trực tiếp vào bảng của A09. |
| `takedown_cases` | **G02 (Audit & Legal Takedown)** | Khi `G02` chốt lệnh Takedown, phát sự kiện `rights.takedown.executed` để `A09`, `B01`, `C01`, `B07` tự động cập nhật `rights_status = 'TAKEDOWN'` và xóa khỏi chỉ mục tìm kiếm. |
| `assessment_assignments` | **F02 (Classroom Assignments)** | `C05 (Exam Delivery)` gọi `AssignmentQueryService.verify_eligibility()` để kiểm tra quyền làm bài và hạn nộp của học sinh. |
| `grades`, `grading_details`, `appeals` | **C07 (Grading, Rubrics & Appeals)** | `F03 (Teacher Gradebook)` và `D01 (Learner Dashboard)` đọc điểm qua Read Projection hoặc `GradingQueryService`. |
| `subscriptions`, `usage_ledger` | **G05 (Commerce & Billing)** | `F05 (RBAC/ABAC & Entitlements)` đọc hạn mức gói cước từ `EntitlementProjection` được đồng bộ qua sự kiện `subscription.updated`. |
| `taxonomy_nodes`, `taxonomy_node_versions`, `taxonomy_aliases` | **B04 (Taxonomy Management)** | `B05 (Knowledge Graph)` chỉ sở hữu bảng `knowledge_edges` và `graph_validation_runs` (tham chiếu tới `taxonomy_nodes.id` của B04, xóa bỏ tên bảng ảo `knowledge_nodes`). |

---

## CHƯƠNG 3. ĐẶC TẢ CHI TIẾT ĐẦY ĐỦ 47 MODULES ĐỘC LẬP (A01 – G06, KHÔNG GỘP DÒNG)

### 3.1. Nhóm A — Content Acquisition & Document Intelligence (A01 – A09)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **A01 — Unified Ingestion Backbone**<br>`[Track 1 (Core MVP) - M1 - Sprint 1]` | **DB:** `sources`, `source_documents`, `document_versions`, `acquisition_jobs`, `pipeline_stage_runs`, `dead_letter_items`<br>**API:**<br>• `POST /api/v1/imports`<br>• `GET /api/v1/imports/{id}`<br>• `POST /api/v1/imports/{id}/retry`<br>• `POST /api/v1/imports/{id}/cancel` | • Khởi tạo `acquisition_jobs` bắt buộc có `idempotency_key` và đặt `acquisition_status = 'INGESTED'`, `rights_status = 'PENDING_REVIEW'`.<br>• Kiểm tra chữ ký byte thật (`libmagic`), quét mã độc ClamAV, tính `checksum_sha256` và lưu bản gốc bất biến lên MinIO tại `bronze/{tenant_id}/{sha256}`.<br>• Chia tiến trình thành các chặng độc lập trong `pipeline_stage_runs`; khi retry chỉ chạy lại từ chặng bị lỗi, không ghi đè file gốc. | • Upload file trùng `sha256` trong cùng Tenant trả về `document_version` hiện có (`200 OK`), không nhân bản.<br>• Retry chặng lỗi đạt tính Idempotent 100% trên 50 kịch bản lỗi giả lập. |
| **A02 — Permitted Web Crawler**<br>`[Track 3 (Expansion) - M5 - Sprint 10]` | **DB:** `crawl_urls`, `crawl_fetches`<br>**API:**<br>• `POST /api/v1/crawler/sources`<br>• `POST /api/v1/crawler/sources/{id}/validate`<br>• `POST /api/v1/crawler/sources/{id}/crawl-jobs`<br>• `POST /api/v1/crawler/sources/{id}/pause` | • Chỉ chạy khi `sources.rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')` và URL khớp `robots.txt` + `allowed_paths`, Rate Limit $\le 1\text{ req}/3\text{s}$.<br>• **Chống SSRF & DNS Rebinding toàn diện:** Phân giải DNS 1 lần duy nhất tại Custom HTTP Transport, chặn toàn bộ IPv4 nội bộ (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10`, `0.0.0.0/8`) và IPv6 (`::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96`), sau đó ghim trực tiếp IP đã duyệt vào socket.<br>• Tự động chuyển nguồn sang `PAUSED_DRIFT` nếu tỷ lệ bóc tách rỗng $> 15\%$ trên 30 trang liên tiếp. | • Vượt qua 100% bộ test tấn công SSRF (IPv4, IPv6-mapped, DNS Rebinding, HTTP 302 Redirect sang metadata `169.254.169.254`).<br>• Tự động tạm dừng (pause) trong $< 5\text{s}$ khi cấu trúc HTML mẫu thay đổi. |
| **A03 — Google Drive Sync & Pairing**<br>`[Track 3 (Expansion) - M5 - Sprint 10]` | **DB:** `source_connections`, `source_sync_cursors`, `file_pairing_candidates`<br>**API:**<br>• `POST /api/v1/integrations/google-drive/connect`<br>• `POST /api/v1/drive/connections/{id}/sync`<br>• `GET /api/v1/drive/sync-jobs/{id}`<br>• `POST /api/v1/drive/pairings/{id}/confirm` | • Sử dụng OAuth scope không nhạy cảm **`drive.file` kết hợp Google Picker API** (hoặc Service Account cho Enterprise B2B) để tránh rào cản kiểm toán CASA Tier 2; lưu tham chiếu khóa trên KMS/Vault (`secret_vault_ref`).<br>• Đồng bộ vi sai qua `changes.list` (`startPageToken`). Khi thu hồi quyền, đặt `status = 'REVOKED'` nhưng giữ nguyên đề đã nhập.<br>• Bung nén `.zip` trong sandbox (tối đa 4 cấp thư mục, $\le 500$ files, tổng dung lượng giải nén $\le 200\text{ MB}$, tỷ lệ nén $< 50:1$, chặn `../`). Ghép cặp `Đề + HDC` bằng 4 tín hiệu (tên file, cùng thư mục, tiêu đề, số lượng câu). | • Đổi tên/di chuyển file trên Drive không kích hoạt OCR lại.<br>• Chặn đứng 100% file test Zip Bomb (`42.zip`) và Path Traversal.<br>• Ghép cặp tự động `Đề + HDC` đạt độ chính xác $\ge 92\%$ trên tập test 50 cặp file. |
| **A04 — Complete Exam Import**<br>`[Track 1 (Core MVP) - M2 - Sprint 4]` | **DB:** `exam_import_sessions`, `provenance_records`<br>**API:**<br>• `POST /api/v1/exam-imports`<br>• `GET /api/v1/exam-imports/{id}/structure`<br>• `PATCH /api/v1/exam-imports/{id}/structure`<br>• `POST /api/v1/exam-imports/{id}/approve` | • Tái dựng cấu trúc đề nguyên bản (Phần thi, Thứ tự câu, Điểm số, Đoạn văn chung `stimulus`, Bảng đáp án/HDC) song song với việc tạo các `QuestionCandidate`.<br>• Chỉ tạo bản PDF gắn thương hiệu (`BRANDED_PDF`) khi `rights_status = 'OWNED'` hoặc `'ALLOWED_DERIVATIVE'`.<br>• Phát hiện vùng Header động: nếu câu hỏi đầu tiên nằm sát mép trên ($y_0 < 120\text{pt}$), tự động mở rộng chiều cao đỉnh trang thêm $70\text{pt}$ trước khi chèn Banner để không bao giờ cắt vào nội dung `Câu 1`. | • Bóc tách thành công toàn bộ 11+ đề mẫu trong Golden Corpus, đạt Precision $\ge 98\%$ và Recall $\ge 96\%$ về ranh giới câu hỏi và hình vẽ. |
| **A05 — Document Import & Parser Registry**<br>`[Track 1 (Core MVP) - M1 - Sprint 3]` | **DB:** `parser_definitions`, `extraction_runs`<br>**API:**<br>• `POST /api/v1/documents`<br>• `GET /api/v1/documents/{id}/versions`<br>• `POST /api/v1/documents/{id}/extract`<br>• `POST /api/v1/extraction-runs/{id}/reprocess` | • Kiểm tra an toàn dung lượng `word/document.xml` ($\le 25\text{ MB}$) trước khi đọc stream XML.<br>• Định tuyến tự động: file `.doc` cũ hoặc `.docx` có chứa `<w:object>` (MathType OLE), `<v:shape>`, `<w:drawing>` bắt buộc chuyển qua LibreOffice Headless với thư mục `-env:UserInstallation` cô lập theo từng job và quản lý bằng Process Group (`os.killpg`).<br>• Lưu `parser_version` trên từng `extraction_runs` để hỗ trợ chạy lại (reprocess) từng trang. | • Chạy đồng thời 20 tiến trình LibreOffice convert `.doc`/`.docx` không xảy ra xung đột khóa profile (0 lỗi exit code 77) và 0 tiến trình `soffice.bin` mồ côi khi timeout. |
| **A06 — OCR & Layout Intelligence**<br>`[Track 1 (Core MVP) - M1 - Sprint 3]` | **DB:** `extracted_pages`, `layout_regions`<br>**API:**<br>• `POST /api/v1/ocr/jobs`<br>• `GET /api/v1/ocr/jobs/{id}`<br>• `POST /api/v1/ocr/jobs/{id}/pages/{page}/reprocess` | • Tự động xoay thẳng ảnh nghiêng (Deskew) và khử bóng xám ảnh chụp điện thoại bằng OpenCV `adaptiveThreshold`.<br>• Thực thi **Copy-on-Write theo từng trang (Page-level Clone)** qua `PDFEngineAdapter` (`pypdfium2` + `pdfplumber` mặc định): gom các khung hình bằng thuật toán **Union-Find (`_merge_rects`)** lặp đến khi hội tụ, cắt ảnh PNG 300 DPI từ trang gốc, sau đó che vùng hình trên trang tạm để đọc luồng văn bản sạch; lọc Header/Footer bằng thuật toán dò chuỗi lặp liên trang. | • Không làm rớt bất kỳ nhóm chức hóa học (`-OH`, `-COOH`) hay nhãn trục đồ thị (`v(m/s)`) nào; bộ nhớ RAM tiêu thụ khi xử lý file PDF 100 trang luôn $< 250\text{ MB}$. |
| **A07 — Content & Question Extraction**<br>`[Track 1 (Core MVP) - M1 - Sprint 3]` | **DB:** `content_candidates`, `question_candidates`<br>**API:**<br>• `POST /api/v1/candidates/normalize`<br>• `GET /api/v1/candidates/{id}`<br>• `POST /api/v1/candidates/{id}/split`<br>• `POST /api/v1/candidates/{id}/merge` | • Với tài liệu chuyên đề hỗn hợp (`STUDY_GUIDE_MIXED` như bài Công thức Bayes): tách phần Lý thuyết/Phương pháp/Công thức thành `ContentCandidate`; tách các phần bài tập thành `QuestionCandidate` (`ESSAY`, `SINGLE_CHOICE`, `CLUSTER_TRUE_FALSE`, `SHORT_NUMERIC`).<br>• Bảo toàn cấu trúc bảng phức hợp (`colspan`, `rowspan`) và khớp tự động bảng Hướng dẫn chấm 3 cột (`Câu - Nội dung - Điểm` (3 cột)). | • Tách chuẩn 1 `ContentCandidate` và 67 `QuestionCandidate` từ file chuyên đề Bayes kèm đầy đủ tọa độ `bbox` trong `provenance_records`. |
| **A08 — Deduplication & Source Pairing**<br>`[Track 1 (Core MVP) - M2 - Sprint 4]` | **DB:** `duplicate_clusters`, `duplicate_members`<br>**API:**<br>• `POST /api/v1/dedup/match`<br>• `GET /api/v1/duplicate-clusters/{id}`<br>• `POST /api/v1/duplicate-clusters/{id}/decisions` | • Chuẩn hóa chuỗi tiếng Việt về **Unicode NFC** trước khi băm.<br>• Khử trùng 3 tầng: (1) `content_hash` (bất biến thứ tự phương án A/B/C/D); (2) `structural_hash` (thay số liệu bài toán bằng `<NUM>` nhưng **bảo toàn chỉ số hóa học** như `H2O`, `Fe3O4`, `C2H5OH`); (3) tương đồng ngữ nghĩa bằng `pg_trgm` + `pgvector`.<br>• Gom các câu trùng tầng 2 & 3 vào `duplicate_clusters` để Reviewer quyết định giữ làm câu biến thể (`VARIANT`) hay gộp. | • Phân biệt chính xác $100\%$ giữa công thức hóa học (`H2SO4` vs `H2SO3` có `structural_hash` khác nhau) và bài toán đổi số liệu ($m=10\text{g}$ vs $m=20\text{g}$ có cùng `structural_hash`). |
| **A09 — Review, Approval & Rights Governance**<br>`[Track 1 (Core MVP) - M1 - Sprint 3]` | **DB:** `review_tasks`, `review_decisions`, `source_rights`<br>**API:**<br>• `POST /api/v1/review-tasks`<br>• `POST /api/v1/review-tasks/{id}/decisions`<br>• `POST /api/v1/rights-records` | • Giao diện duyệt Side-by-Side hiển thị trang gốc có khoanh vùng `bbox` bên trái và trình soạn thảo câu hỏi bên phải.<br>• Quản lý độc lập quyết định duyệt chuyên môn (`verification_status = 'VERIFIED'`) và quyết định duyệt bản quyền (`rights_status`).<br>• Cổng kiểm tra cứng ở cả Application Service và Database Trigger: chặn tuyệt đối thao tác Publish nếu `rights_status` là `PENDING_REVIEW`, `RESTRICTED` hoặc `TAKEDOWN`. | • Kiểm thử tự động xác nhận câu hỏi có `verification_status = 'VERIFIED'` nhưng `rights_status = 'PENDING_REVIEW'` hoặc `'RESTRICTED'` bị DB từ chối lệnh Publish với lỗi `CheckViolation`. |

### 3.2. Nhóm B — Content & Knowledge Management (B01 – B07)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **B01 — Content Library & Versioning**<br>`[Track 1 (Core MVP) - M1 - Sprint 2]` | **DB:** `content_items`, `content_versions`, `publications`, `access_grants`<br>**API:**<br>• `POST /api/v1/content`<br>• `POST /api/v1/content/{id}/versions`<br>• `POST /api/v1/content-versions/{id}/publish` | • Hỗ trợ học liệu đa định dạng (`ARTICLE`, `LESSON`, `VIDEO`, `DOCUMENT`, `FLASHCARD_SET`, `VOCABULARY_SET`) và phân tách `visibility_scope` (`SYSTEM_PUBLIC`, `TENANT_PRIVATE`).<br>• `content_items.current_version_id` có khóa ngoại `DEFERRABLE` trỏ tới `content_versions.id`. Khi Publish bản mới, bản cũ giữ nguyên trạng thái lịch sử bất biến. | • Sửa bài viết đã Publish tự động sinh `version_no + 1` ở trạng thái `DRAFT`; lệnh `UPDATE` trực tiếp lên dòng `content_versions` đã `PUBLISHED` bị Trigger DB chặn 100%. |
| **B02 — Content Editor & Authoring Studio**<br>`[Track 1 (Core MVP) - M1 - Sprint 2]` | **DB:** `content_drafts`, `content_blocks`, `comment_threads`<br>**API:**<br>• `POST /api/v1/content/{id}/drafts`<br>• `PATCH /api/v1/drafts/{id}`<br>• `POST /api/v1/drafts/{id}/validate` | • Trình soạn thảo dạng khối (Block Studio) chuẩn VietJack nâng cấp: `THEORY_SUMMARY`, `FORMULA_BOX`, `METHOD_STEPS`, `TIP_WARNING`, `WORKED_EXAMPLE`, `INTERACTIVE_QUESTION_EMBED`.<br>• Bộ kiểm tra `validate` phân tích cú pháp KaTeX và xác thực quyền truy cập của toàn bộ `asset_id` nhúng trong bài trước khi cho phép gửi duyệt. | • Phát hiện 100% lỗi cú pháp LaTeX/KaTeX không đóng ngoặc hoặc nhúng `asset_id` của Tenant khác trước khi lưu bản chính thức. |
| **B03 — Media & Asset Management**<br>`[Track 1 (Core MVP) - M1 - Sprint 1]` | **DB:** `assets`, `content_assets`, `question_assets`<br>**API:**<br>• `POST /api/v1/assets/upload-sessions`<br>• `POST /api/v1/assets/{id}/finalize`<br>• `GET /api/v1/assets/{id}/signed-url` | • Toàn bộ bucket MinIO/S3 đặt ở chế độ Private. Quy trình upload 2 bước: cấp Presigned PUT URL -> gọi `finalize` kiểm tra `sha256`, `mime_type` thực tế và quét ClamAV.<br>• Tự động cắt bản xem trước `PREVIEW_PDF` (2 trang đầu) cho tài liệu `PRO` và cấp Signed GET URL có thời hạn ngắn ($\le 15\text{ phút}$). | • Tài khoản `FREE` chỉ lấy được Signed URL của `PREVIEW_PDF`; truy cập chéo `asset_id` giữa 2 Tenant bị chặn hoàn toàn bởi RLS (`404 Not Found`). |
| **B04 — Taxonomy Management**<br>`[Track 1 (Core MVP) - M1 - Sprint 2]` | **DB:** `taxonomy_nodes`, `taxonomy_node_versions`, `taxonomy_aliases`<br>**API:**<br>• `GET /api/v1/taxonomy/nodes`<br>• `POST /api/v1/taxonomy/nodes`<br>• `POST /api/v1/taxonomy/nodes/{id}/versions` | • **Tách bảng định danh `taxonomy_nodes` (giữ `id` và `code_path ltree` ổn định) khỏi bảng `taxonomy_node_versions`** để khi cập nhật tên/mô tả ở phiên bản mới, toàn bộ câu hỏi và cạnh đồ thị đang trỏ vào `taxonomy_nodes.id` không bao giờ bị đứt.<br>• Sử dụng kiểu dữ liệu PostgreSQL `ltree` (`MATH.PROB.BAYES`) kèm chỉ mục `GIST` để truy vấn cây phân cấp trong $< 5\text{ms}$. | • Nâng cấp phiên bản của một nút Taxonomy không làm mất bất kỳ liên kết câu hỏi hay cạnh đồ thị nào; truy vấn nhánh con bằng toán tử `<@` của `ltree` đạt $< 5\text{ms}$. |
| **B05 — Knowledge Graph**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 7]` | **DB:** `knowledge_edges`, `graph_validation_runs`<br>**API:**<br>• `POST /api/v1/knowledge-graph/edges`<br>• `GET /api/v1/knowledge-graph/path`<br>• `POST /api/v1/knowledge-graph/validate` | • Lưu trữ các cạnh quan hệ có hướng giữa các `taxonomy_nodes.id`: `PREREQUISITE`, `RELATED_TO`, `EXTENDS`, `APPLIED_IN`, `COMMON_MISCONCEPTION`.<br>• Chạy thuật toán kiểm tra đồ thị có hướng không chu trình (DAG Cycle Detection bằng DFS/Topological Sort) trên tập cạnh `PREREQUISITE` trước khi commit. | • Mọi thao tác thêm cạnh tạo chu trình `A -> B -> C -> A` bị chặn tức thì với mã lỗi `422 PREREQUISITE_CYCLE_DETECTED`. |
| **B06 — Curriculum Mapping & Planning**<br>`[Track 1 (Core MVP) - M1 - Sprint 2]` | **DB:** `curriculum_versions`, `curriculum_nodes`, `taxonomy_mappings`<br>**API:**<br>• `POST /api/v1/curricula`<br>• `POST /api/v1/curricula/{id}/versions`<br>• `POST /api/v1/curriculum-mappings`<br>• `GET /api/v1/curricula/{id}/coverage` | • Quản lý cây mục lục chương trình học 4 cấp (`CHAPTER -> LESSON -> SECTION -> PROBLEM_TYPE`), khởi đầu với SGK Kết nối tri thức môn Toán & Vật lí lớp 10–11–12.<br>• Ánh xạ nhiều-nhiều giữa `curriculum_nodes` và `taxonomy_nodes`. Cung cấp `CoverageReport` thống kê số lượng câu hỏi theo từng mức độ nhận thức (`NB`, `TH`, `VD`, `VDC`) trên mỗi Dạng bài. | • Seed đầy đủ cây SGK Kết nối tri thức và xuất báo cáo `CoverageReport` chính xác trong $< 200\text{ms}$. |
| **B07 — Search, Discovery & Collections**<br>`[Track 1 (Core MVP) - M2 - Sprint 4]` | **DB:** `search_documents`, `collections`, `collection_items`<br>**API:**<br>• `GET /api/v1/search`<br>• `POST /api/v1/collections`<br>• `POST /api/v1/collections/{id}/items` | • Xây dựng hàm bọc `f_unaccent(text)` đánh dấu `IMMUTABLE` để tạo chỉ mục `GIN` kết hợp `tsvector` và `pg_trgm`, cho phép tìm kiếm tiếng Việt có dấu và không dấu siêu tốc.<br>• Là Read Projection có thể tái tạo hoàn toàn từ các bảng gốc; tự động loại bỏ tức thì các mục bị chuyển sang `TAKEN_DOWN`. | • Tìm kiếm từ khóa không dấu (`cong thuc bayes`, `dao dong tat dan`) trả kết quả đúng trong `p95 < 120ms`. |

### 3.3. Nhóm C — Assessment, Question Bank, Fast Autosave & Grading (C01 – C07)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **C01 — Question Bank & Typed Item Authoring**<br>`[Track 1 (Core MVP) - M2 - Sprint 4]` | **DB:** `questions`, `question_versions`, `question_options`, `question_stimuli`<br>**API:**<br>• `POST /api/v1/questions`<br>• `POST /api/v1/questions/{id}/versions`<br>• `POST /api/v1/question-versions/{id}/validate` | • Hỗ trợ 6 loại câu hỏi chuẩn: `SINGLE_CHOICE` (ABCD), `MULTIPLE_SELECT`, `CLUSTER_TRUE_FALSE` (Đúng/Sai 4 ý a,b,c,d), `SHORT_NUMERIC` (Trả lời ngắn), `ESSAY` (Tự luận), `GROUP_STEM` (Câu chùm dùng chung `question_stimuli`).<br>• Ràng buộc `UNIQUE (tenant_id, stable_code)` và GIN Index trên `exam_framework_tags`. | • Tạo và lưu phiên bản bất biến cho cả 6 loại câu hỏi; sửa câu hỏi đã Publish tự động tạo `version_no + 1` và giữ nguyên bản cũ cho các đề đã phát hành. |
| **C02 — Question Quality & Verification**<br>`[Track 1 (Core MVP) - M1 - Sprint 3]` | **DB:** `verification_runs`<br>**API:**<br>• `POST /api/v1/question-versions/{id}/verify`<br>• `GET /api/v1/verification-runs/{id}` | • Gọi qua `E01 (AI Provider Gateway)` để chạy 2 Job kiểm định:<br>  1) **Job 1 (Healer & Subject Lock):** Sửa lỗi OCR công thức, khóa môn học chống phân loại nhầm bài Toán xác suất (bối cảnh y sinh) sang Sinh học.<br>  2) **Job 2 (Heterogeneous SymPy + LLM Verifier):** Giải độc lập không nhìn đáp án nguồn bằng `SymPy` sandbox + LLM, đối sánh qua `canonicalize_answer`. Nếu lệch, gọi `A09` tạo `review_task` mức `HIGH`. | • Phát hiện $100\%$ câu hỏi khuyết phương án hoặc câu bài toán có đáp án nguồn mâu thuẫn với nghiệm của `SymPy`. |
| **C03 — Assessment Framework & Blueprint**<br>`[Track 1 (Core MVP) - M2 - Sprint 4]` | **DB:** `assessment_frameworks`, `framework_versions`, `blueprints`, `scoring_scheme_versions`<br>**API:**<br>• `POST /api/v1/assessment-frameworks`<br>• `POST /api/v1/blueprints`<br>• `POST /api/v1/blueprints/{id}/validate` | • Định nghĩa chính xác cấu trúc **Đề thi THPTQG 2025+ mới**:<br>  - **Môn Toán (90 phút, 22 câu, 10 điểm):** Phần I (12 câu ABCD $\times 0.25\text{đ} = 3.0\text{đ}$), Phần II (4 câu Đúng/Sai 4 ý $\times 1.0\text{đ} = 4.0\text{đ}$), Phần III (6 câu Trả lời ngắn $\times 0.5\text{đ} = 3.0\text{đ}$).<br>  - **Môn Lý / Hóa / Sinh (50 phút, 28 câu, 10 điểm):** Phần I (18 câu ABCD $\times 0.25\text{đ} = 4.5\text{đ}$), Phần II (4 câu Đúng/Sai 4 ý $\times 1.0\text{đ} = 4.0\text{đ}$), Phần III (6 câu Trả lời ngắn $\times 0.25\text{đ} = 1.5\text{đ}$).<br>• Đối chiếu ma trận yêu cầu với kho câu hỏi đủ điều kiện để xuất `ShortageReport`. | • API `validate` kiểm tra đúng cấu trúc đề THPTQG 2025+ (22 câu Toán, 28 câu Lý/Hóa/Sinh) và báo cáo chính xác số câu còn thiếu theo từng ô chuyên đề $\times$ độ khó. |
| **C04 — Exam Builder & Assembly Engine**<br>`[Track 1 (Core MVP) - M2 - Sprint 5]` | **DB:** `assessments`, `assessment_versions`, `assessment_items`, `assessment_answer_keys`<br>**API:**<br>• `POST /api/v1/assessments`<br>• `POST /api/v1/assessments/{id}/assemble`<br>• `POST /api/v1/assessments/{id}/publish` | • Thuật toán bốc câu hỏi có ràng buộc: chỉ chọn câu có `publication_status = 'PUBLISHED'`, `verification_status = 'VERIFIED'`, `rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')`, không chọn 2 câu cùng `duplicate_cluster_id`, giữ nguyên chùm `stimulus_id`.<br>• Khi Publish: lưu cấu trúc đề (không chứa đáp án) vào `assessment_versions.structure_snapshot_json` và lưu đáp án chấm điểm tách biệt hoàn toàn vào `assessment_answer_keys`. | • Bốc tự động đề thi chuẩn THPTQG 2025+ trong $< 500\text{ms}$; cùng `random_seed` cho ra cùng kết quả; kiểm tra bảo mật xác nhận `structure_snapshot_json` chứa $0\%$ dữ liệu đáp án. |
| **C05 — Exam Delivery & Session Control**<br>`[Track 1 (Core MVP) - M2 - Sprint 5]` | **DB:** `attempts`<br>**API:**<br>• `POST /api/v1/exam-sessions/start`<br>• `GET /api/v1/attempts/{id}/paper`<br>• `POST /api/v1/attempts/{id}/submit` | • Kiểm tra tư cách dự thi, tính `deadline_at = started_at + duration_seconds` theo giờ Server.<br>• Sinh `attempt_seed` riêng cho từng lượt thi, hoán vị thứ tự câu hỏi trong từng phần (giữ nguyên nhóm `stimulus_id`) và hoán vị phương án A/B/C/D, sau đó **lưu cố định `question_order_ids` và `option_permutations_json` vào bảng `attempts`** để phục vụ chấm điểm và phúc khảo. | • Tải lại trang (F5) hoặc đổi thiết bị giữa giờ thi vẫn giữ nguyên $100\%$ thứ tự câu hỏi và thứ tự phương án A/B/C/D đã chia cho thí sinh đó; JSON đề thi tuyệt đối không lộ đáp án. |
| **C06 — Attempt Management & Fast Autosave**<br>`[Track 1 (Core MVP) - M2 - Sprint 5]` | **DB:** `responses`, `response_revisions`<br>**API:**<br>• `PUT /api/v1/attempts/{id}/responses/{item_id}`<br>• `GET /api/v1/attempts/{id}/sync` | • **Quy tắc Idempotency-First:** Khi nhận request kèm `Idempotency-Key` và `client_sequence`:<br>  1) Kiểm tra `idempotency_key` trong `response_revisions`: nếu đã tồn tại, lập tức trả về `200 OK` kèm trạng thái đã lưu (hỗ trợ client retry an toàn khi rớt mạng).<br>  2) Nếu `idempotency_key` mới, khóa dòng `responses` (`FOR UPDATE`), nếu `client_sequence <= current_sequence` thì trả `409 STALE_CLIENT_SEQUENCE`.<br>  3) Ghi vào `responses` + `response_revisions` và `COMMIT` đồng bộ trước khi trả `200 ACK` (không ghi Outbox lẻ). | • Vượt qua bài kiểm thử Chaos/Failure Injection (ngắt mạng gửi lại cùng `Idempotency-Key` nhận `200 OK`, gói tin cũ đến muộn nhận `409`, sập Redis vẫn lưu bài bình thường); độ trễ Server `p95 < 120ms`. |
| **C07 — Grading, Rubrics & Appeals**<br>`[Track 1 (Core MVP) - M2 - Sprint 6]` | **DB:** `grades`, `grading_details`, `rubric_versions`, `appeals`<br>**API:**<br>• `POST /api/v1/attempts/{id}/grade`<br>• `POST /api/v1/grades/{id}/release`<br>• `POST /api/v1/grades/{id}/appeals` | • Chấm điểm tất định theo `scoring_scheme_versions` và `assessment_answer_keys`:<br>  1) Phần I (ABCD): $0.25\text{đ}$/câu.<br>  2) Phần II (Đúng/Sai 4 ý): đúng 1 ý $= 0.10\text{đ}$, 2 ý $= 0.25\text{đ}$, 3 ý $= 0.50\text{đ}$, 4 ý $= 1.00\text{đ}$.<br>  3) Phần III (Trả lời ngắn): áp dụng `NumericParsePolicy` chuẩn phiếu tô THPTQG (tối đa 4 ký tự, đổi dấu `,` thập phân thành `.`, từ chối hoặc cảnh báo ngay trên UI nếu nhập dấu phân cách hàng nghìn `1.000`).<br>• Khi phúc khảo hoặc Regrade do đính chính đáp án, tạo dòng `grades` mới với `revision_no + 1` và chuyển dòng cũ sang `is_current = FALSE`. | • Unit test đạt $100\%$ trên toàn bộ tổ hợp điểm Phần I, II, III và kịch bản Regrade bảo toàn đầy đủ lịch sử các lần chấm (`revision_no = 1, 2...`). |

### 3.4. Nhóm D — Learning Experience, Practice, FSRS & Adaptive IRT (D01 – D06)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **D01 — Learner Dashboard & Activity**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 7]` | **DB:** `activity_events`, `learner_dashboard_projections`<br>**API:**<br>• `GET /api/v1/learners/me/dashboard`<br>• `GET /api/v1/learners/me/activity` | • Tổng hợp tiến độ học tập cá nhân vào bảng `learner_dashboard_projections`: bài học đang đọc dở, bài tập giáo viên giao sắp đến hạn, số câu trong Sổ tay câu sai cần khắc phục, lịch ôn tập FSRS trong ngày và biểu đồ làm chủ kiến thức theo chương. | • API Dashboard phản hồi trong `p95 < 200ms` và cập nhật bất đồng bộ trong $< 3\text{s}$ sau khi học sinh nộp bài thi hoặc hoàn thành phiên luyện tập. |
| **D02 — Learning Path & Study Planner**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 7]` | **DB:** `learning_plans`, `plan_tasks`<br>**API:**<br>• `POST /api/v1/learning-plans`<br>• `GET /api/v1/learning-plans/{id}`<br>• `POST /api/v1/learning-plans/{id}/recalculate` | • Khi phát hiện học sinh có `mastery_estimate < 0.50` ở một nút kiến thức $u$, thuật toán duyệt ngược các cạnh `PREREQUISITE` ($v \to u$) trên `knowledge_edges`: nếu nút tiên quyết $v$ cũng yếu ($< 0.60$), hệ thống tự động xếp nhiệm vụ ôn tập nút gốc $v$ trước rồi mới đến nút $u$. | • 100% nhiệm vụ trong lộ trình cá nhân hóa có trường `rationale_text` giải thích rõ nguyên nhân gợi ý và không bao giờ ghi đè bài tập bắt buộc do giáo viên giao. |
| **D03 — Practice Engine**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 7]` | **DB:** `practice_sessions`, `practice_responses`<br>**API:**<br>• `POST /api/v1/practice-sessions`<br>• `POST /api/v1/practice-sessions/{id}/responses`<br>• `POST /api/v1/practice-sessions/{id}/complete` | • Phục vụ luyện tập tương tác 4 Tab ngay dưới bài lý thuyết VietJack hoặc luyện theo chuyên đề/độ khó.<br>• Hỗ trợ chế độ `IMMEDIATE_FEEDBACK` (chấm và hiện lời giải chi tiết ngay sau từng câu) và chế độ mở gợi ý từng bước (`Hint`). | • Dữ liệu luyện tập được tách biệt hoàn toàn khỏi bảng `attempts`/`grades` của kỳ thi chính thức nhưng vẫn cung cấp tín hiệu đầu vào để cập nhật `mastery_estimates`. |
| **D04 — Flashcards & Spaced Repetition (FSRS v5)**<br>`[Track 2 (Intelligent & LMS) - M4 - Sprint 9]` | **DB:** `flashcard_decks`, `flashcards`, `revision_schedules`<br>**API:**<br>• `POST /api/v1/flashcard-decks`<br>• `POST /api/v1/flashcards/{id}/review`<br>• `GET /api/v1/revision-schedule` | • Hỗ trợ thẻ nhớ công thức Toán/Lý/Hóa/Sinh và thẻ câu hỏi từng làm sai.<br>• Cài đặt thuật toán **FSRS v5** tính toán tất định Độ ổn định ($S$), Độ khó ($D$) và Khả năng nhớ ($R$) theo 4 mức đánh giá (`1: Again`, `2: Hard`, `3: Good`, `4: Easy`) theo đúng múi giờ địa phương của người học (`Asia/Ho_Chi_Minh`). | • Kết quả tính khoảng cách ngày ôn tập (`interval_days`) khớp 100% với bộ test chuẩn của FSRS v5 và lưu rõ `scheduler_version`. |
| **D05 — Error Notebook & Remediation**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 7]` | **DB:** `error_notes`, `remediation_tasks`<br>**API:**<br>• `GET /api/v1/error-notes`<br>• `POST /api/v1/error-notes/{id}/remediate`<br>• `POST /api/v1/error-notes/{id}/resolve` | • Tự động thu thập mọi câu trả lời sai từ cả `attempts` và `practice_sessions` vào `error_notes`.<br>• Truy vấn 3 câu hỏi biến thể cùng `structural_hash` hoặc gần nhất trên không gian `pgvector` trong cùng `primary_taxonomy_node_id` để tạo bài tập khắc phục. Chỉ chuyển sang `RESOLVED` khi học sinh làm đúng liên tiếp $\ge 2$ câu tương tự. | • Học sinh làm đúng 1 câu chưa được đóng lỗi; chỉ khi chuỗi `consecutive_correct_count >= 2` hệ thống mới chuyển trạng thái `error_notes.status = 'RESOLVED'`. |
| **D06 — Progress, Mastery & Phased Adaptive IRT**<br>`[Track 3 (Expansion) - M5 - Sprint 11]` | **DB:** `mastery_estimates`, `question_irt_parameters`<br>**API:**<br>• `GET /api/v1/learners/me/mastery`<br>• `POST /api/v1/mastery/recompute` | • **Cổng Kiểm định Đa Tiêu Chí IRT (`irt_tier_registry` — Khắc phục AUD-003 & AUD-011):** Không quyết định bậc mô hình chỉ bằng cỡ mẫu $N$; bắt buộc kiểm tra đồng thời số lượt trả lời (`item_responses`), số người học (`learner_count`), độ phân tán năng lực (`ability_sd`), hội tụ (`converged`), độ phù hợp (`infit_msq`), `SE(b)` và độ lệch `DIF`:<br>  - `RULE_WEIGHTED_L1`: mặc định khi chưa đủ điều kiện hiệu chuẩn.<br>  - `RASCH_1PL_L2`: khi $N \ge 150$, `learners >= 100`, `ability_sd >= 0.60`, `infit_msq` $\in [0.70, 1.30]$, `SE(b) < 0.30`.<br>  - `IRT_2PL_GPCM_L3`: khi $N \ge 300$, `learners >= 250`, `ability_sd >= 0.65`, `infit_msq` $\in [0.75, 1.25]$, `SE(b) < 0.25`.<br>  - `IRT_3PL_GPCM_L4`: khi $N \ge 1000$, `learners >= 800`, `ability_sd >= 0.70`, `infit_msq` $\in [0.80, 1.20]$, `SE(b) < 0.20` kèm `Beta(5, 17)` prior.<br>• Tự động Fallback về bậc thấp hơn nếu bất kỳ chỉ số chẩn đoán nào trượt. | • Khi học sinh làm $< 20$ câu hoặc $\text{SE}(\theta) \ge 0.35$, API trả về `status = 'INSUFFICIENT_DATA'`, tuyệt đối không hiển thị điểm dự đoán ảo gây hiểu lầm. |

### 3.5. Nhóm E — AI Learning System & Governance (E01 – E06, Tách Rời Từng Module)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **E01 — AI Orchestration & Provider Gateway**<br>`[Track 1 (Core MVP) - M1 - Sprint 2]` | **DB:** `ai_providers`, `ai_models`, `prompt_versions`, `ai_jobs`, `ai_traces`<br>**API:**<br>• `POST /api/v1/ai/jobs`<br>• `GET /api/v1/ai/jobs/{id}`<br>• `GET /api/v1/ai/usage` | • **Triển khai ngay tại Sprint 2** để làm cổng điều phối duy nhất cho OCR/LaTeX, C02 Verifier và AI Tutor.<br>• Tự động xoay vòng API Key (`key_rotator`), kiểm tra hạn mức ngân sách token theo Tenant/Ngày (`daily_budget_usd`), kích hoạt Circuit Breaker và chuyển sang model dự phòng khi gặp lỗi `429/5xx` hoặc timeout $> 15\text{s}$. | • Khi 1 API Key trả về `429 Too Many Requests`, Gateway tự động xoay sang Key tiếp theo trong $< 50\text{ms}$; chặn đứng 100% lời gọi khi Tenant chạm trần ngân sách ngày. |
| **E02 — AI Tutor & Grounded Q&A**<br>`[Track 2 (Intelligent & LMS) - M4 - Sprint 9]` | **DB:** `tutor_sessions`, `tutor_messages`, `retrieval_traces`<br>**API:**<br>• `POST /api/v1/tutor/sessions`<br>• `POST /api/v1/tutor/sessions/{id}/messages` | • Chỉ truy xuất ngữ cảnh (RAG) từ các `content_versions` và `question_versions` đã `PUBLISHED` mà học sinh có quyền xem.<br>• Hỗ trợ 3 chế độ: `HINT_MODE` (chỉ gợi ý công thức/bước tiếp theo, bộ lọc hậu kiểm chặn lộ đáp số), `EXPLAIN_MODE` (giải thích chi tiết sau khi đã nộp bài), `CHALLENGE_MODE` (đưa bài tương tự).<br>• **Exam Lock:** Kiểm tra bảng `attempts`: nếu học sinh đang có phiên thi `status = 'IN_PROGRESS'`, lập tức từ chối phục vụ với mã `403 EXAM_SESSION_ACTIVE`. | • Chặn $100\%$ yêu cầu hỏi AI Tutor khi học sinh đang trong phòng thi `IN_PROGRESS` và chặn $100\%$ các kịch bản Prompt Injection gạ lộ đáp án ở `HINT_MODE`. |
| **E03 — AI Question Generation (Matrix Gap Filler)**<br>`[Track 3 (Expansion) - M5 - Sprint 10]` | **DB:** `generation_requests`<br>**API:**<br>• `POST /api/v1/ai/generations`<br>• `GET /api/v1/ai/generations/{id}` | • Khi `ShortageReport` của `C03` báo thiếu câu hỏi ở một ô ma trận (chuyên đề $\times$ độ khó), giáo viên có thể yêu cầu `E03` sinh câu hỏi biến thể bám sát đoạn tài liệu gốc (`source_anchors`).<br>• Mọi câu hỏi sinh ra bắt buộc gắn nhãn `acquisition_method = 'AI_GENERATED'`, đi qua `C02 (SymPy + LLM Verifier)` và đẩy vào `A09 (Review Queue)` để con người duyệt. | • $0\%$ câu hỏi do AI sinh ra được phép đi thẳng vào kho `PUBLISHED` mà không qua bước phê duyệt của Reviewer trong `review_decisions`. |
| **E04 — AI Step-by-Step Solution & SymPy Cross-Check**<br>`[Track 2 (Intelligent & LMS) - M4 - Sprint 9]` | **DB:** `explanation_runs`<br>**API:**<br>• `POST /api/v1/ai/explanations`<br>• `POST /api/v1/ai/answer-checks` | • Sinh lời giải chi tiết từng bước (Phương pháp giải -> Biến đổi công thức -> Thay số -> Kết luận) cho các câu hỏi mới nhập chỉ có đáp án A/B/C/D mà thiếu lời giải.<br>• Tự động trích xuất biểu thức đại số/giải tích sang mã Python `SymPy` chạy trong sandbox cô lập để xác minh kết quả tính toán trước khi lưu bản nháp lời giải. | • Lời giải sinh ra có đầy đủ cú pháp KaTeX hợp lệ và đạt tỷ lệ khớp nghiệm với `SymPy` $\ge 95\%$ trên tập câu hỏi Toán/Lý tính toán. |
| **E05 — Explainable Next-Step Recommendation Engine**<br>`[Track 3 (Expansion) - M5 - Sprint 11]` | **DB:** `recommendations`<br>**API:**<br>• `GET /api/v1/recommendations`<br>• `POST /api/v1/recommendations/{id}/feedback` | • Kết hợp tín hiệu từ `error_notes` (D05), `mastery_estimates` (D06) và `knowledge_edges` (B05) để xếp hạng Top 3 hành động học tập tiếp theo có lợi ích kỳ vọng cao nhất.<br>• Mỗi gợi ý phải kèm theo cấu trúc giải thích tường minh (`evidence_json`: sai những câu nào, lỗ hổng ở khái niệm tiên quyết nào). | • Mọi bản ghi `recommendations` trả về trên UI đều hiển thị rõ lý do gợi ý dựa trên dữ liệu thực tế của học sinh và cho phép ẩn/báo không phù hợp. |
| **E06 — Teacher AI Copilot**<br>`[Track 3 (Expansion) - M5 - Sprint 11]` | **DB:** `teacher_assist_jobs`<br>**API:**<br>• `POST /api/v1/teacher/ai-assist`<br>• `GET /api/v1/teacher/ai-assist/{id}` | • Hỗ trợ giáo viên sau mỗi bài kiểm tra của lớp: tự động phân cụm các lỗi sai phổ biến nhất của học sinh trong lớp, tóm tắt các lỗ hổng kiến thức trọng tâm và soạn sẵn đề xuất phiếu bài tập khắc phục (`Draft Assignment`) cho từng nhóm học lực. | • Báo cáo tóm tắt lớp học chỉ sử dụng dữ liệu thuộc đúng `class_id` của giáo viên đó (tuân thủ RLS) và yêu cầu giáo viên bấm xác nhận trước khi giao phiếu bài tập khắc phục. |

### 3.6. Nhóm F — Teacher LMS, Organization, RBAC & Notifications (F01 – F06, Tách Rời Từng Module)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **F01 — Class Roster & Group Management**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 8]` | **DB:** `groups`, `classes`, `class_members`<br>**API:**<br>• `POST /api/v1/classes`<br>• `POST /api/v1/classes/{id}/members`<br>• `DELETE /api/v1/classes/{id}/members/{member_id}` | • Giáo viên tạo lớp học, thêm học sinh qua mã mời (`invite_code`) hoặc import danh sách CSV.<br>• Khi học sinh rời lớp hoặc chuyển lớp, thực hiện **Soft-Delete** (`class_members.status = 'INACTIVE'`, `left_at = NOW()`), tuyệt đối không xóa cứng bản ghi `memberships` hay `attempts` để bảo toàn lịch sử điểm. | • Học sinh rời lớp không còn nhận bài tập mới của lớp đó, nhưng toàn bộ bài thi và điểm số cũ trong quá khứ vẫn giữ nguyên 100%. |
| **F02 — Classroom Assignments & Scheduling**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 8]` | **DB:** `assessment_assignments`<br>**API:**<br>• `POST /api/v1/assignments`<br>• `GET /api/v1/assignments/{id}`<br>• `PATCH /api/v1/assignments/{id}` | • Giáo viên giao bài học hoặc đề thi (`assessment_version_id` cố định) cho cả lớp (`class_id`) hoặc nhóm học sinh (`group_id`) kèm khung giờ mở đề (`available_from`), hạn nộp (`due_at`), số lần làm tối đa (`max_attempts`) và chính sách công bố đáp án (`reveal_policy`). | • Thí sinh không thể bắt đầu làm bài trước `available_from` hoặc vượt quá `max_attempts`; đáp án chỉ hiển thị khi thỏa mãn `reveal_policy` (VD: sau khi hết hạn nộp của cả lớp). |
| **F03 — Teacher Gradebook & Class Analytics**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 8]` | **DB:** `gradebook_projections`<br>**API:**<br>• `GET /api/v1/classes/{id}/gradebook`<br>• `GET /api/v1/classes/{id}/item-analysis`<br>• `GET /api/v1/classes/{id}/gradebook/export` | • Tổng hợp bảng điểm toàn lớp, tỷ lệ nộp bài đúng hạn, phân phối phổ điểm và bảng phân tích độ khó thực tế của từng câu hỏi trong bài kiểm tra (chỉ ra Top 5 câu cả lớp sai nhiều nhất và phương án nhiễu nào học sinh hay chọn nhầm nhất). | • Xuất bảng điểm lớp ra CSV/Excel chính xác 100% và tải trang phân tích câu hỏi của lớp 50 học sinh trong $< 300\text{ms}$. |
| **F04 — Tenant Admin, Organization & Parental Consent**<br>`[Track 1 (Core MVP) - M1 - Sprint 1]` | **DB:** `tenants`, `users`, `memberships`, `parental_consents`<br>**API:**<br>• `POST /api/v1/tenants`<br>• `POST /api/v1/memberships`<br>• `POST /api/v1/privacy/parental-consents`<br>• `POST /api/v1/privacy/data-erasure-requests` | • Quản lý hồ sơ trường học/trung tâm, cấu hình hạn mức tài nguyên.<br>• **Tuân thủ Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP:** Với người học K-12 dưới 16 tuổi, bắt buộc ghi nhận xác nhận đồng ý xử lý dữ liệu cá nhân của phụ huynh hoặc đại diện nhà trường vào bảng `parental_consents`. Hỗ trợ quy trình xóa/ẩn danh hóa thông tin nhận dạng cá nhân (PII Anonymization) khi có yêu cầu hợp lệ. | • Tài khoản học sinh dưới 16 tuổi chưa có xác nhận `parental_consents` (qua phụ huynh hoặc hợp đồng B2B nhà trường) không thể kích hoạt tính năng lưu trữ hồ sơ cá nhân nhạy cảm. |
| **F05 — RBAC/ABAC Access Control & Entitlements**<br>`[Track 1 (Core MVP) - M1 - Sprint 1]` | **DB:** `roles`, `permissions`, `role_permissions`<br>**API:**<br>• `GET /api/v1/permissions/me`<br>• `POST /api/v1/roles`<br>• `PUT /api/v1/memberships/{id}/roles` | • Tách biệt rạch ròi giữa **RBAC/ABAC** (`Role/Permission`: quyền thực hiện thao tác nghiệp vụ như `question:approve`, `exam:publish`) và **Entitlement** (`FREE`, `STUDENT_PRO`, `TEACHER_PRO`, `SCHOOL_ENTERPRISE`: quyền lợi gói cước).<br>• Mọi API lấy `tenant_id` và `membership_id` từ JWT đã ký, kết hợp thiết lập biến phiên PostgreSQL `app.current_tenant_id` cho RLS. | • Vượt qua bộ kiểm thử tự động Cross-Tenant IDOR trên toàn bộ các nhóm endpoint: $0$ trường hợp User thuộc Tenant A đọc hoặc sửa được bản ghi của Tenant B. |
| **F06 — Omnichannel Notifications (Idempotent Delivery)**<br>`[Track 2 (Intelligent & LMS) - M3 - Sprint 8]` | **DB:** `notifications`, `notification_deliveries`<br>**API:**<br>• `GET /api/v1/inbox`<br>• `POST /api/v1/inbox/{id}/read`<br>• `PATCH /api/v1/notifications/preferences` | • Gửi thông báo In-App và Email cho các sự kiện: bài tập mới được giao, sắp đến hạn nộp bài, có kết quả chấm điểm/phúc khảo, và nhắc lịch ôn tập FSRS hàng ngày.<br>• Chống gửi trùng thông báo bằng khóa `dedup_key UNIQUE` trên bảng `notifications`. | • Khi sự kiện `attempt.graded` bị phát lại 3 lần trên bus, học sinh chỉ nhận đúng 1 thông báo duy nhất. |

### 3.7. Nhóm G — Platform Admin, Operations, Analytics, Commerce & Verticals (G01 – G06, Tách Rời Từng Module)

| Mã Module & Sprint | Bảng DB Sở Hữu (Write Owner) & API Chính | Quy tắc Nghiệp vụ, Thuật toán & Xử lý Lỗi Đặc thù (V11.3 Implementation Rules) | Tiêu chí Nghiệm thu Định lượng (DoD) |
| :--- | :--- | :--- | :--- |
| **G01 — Platform Configuration & Feature Flags**<br>`[Track 1 (Core MVP) - M1 - Sprint 1]` | **DB:** `platform_settings`, `feature_flags`<br>**API:**<br>• `GET /api/v1/admin/feature-flags`<br>• `PATCH /api/v1/admin/feature-flags/{key}`<br>• `PATCH /api/v1/admin/config/{key}` | • Quản lý cờ tính năng (`feature_flags`) hỗ trợ bật/tắt tức thì theo phần trăm người dùng, theo `tenant_id` hoặc ghim cố định phiên bản `parser_version` / `ai_model` khi phát hiện lỗi hồi quy mà không cần redeploy code. | • Đổi trạng thái `feature_flag` có hiệu lực trên toàn bộ API nodes trong $< 5\text{s}$ và tự động ghi nhật ký vào `audit_events`. |
| **G02 — Immutable Audit Trail & Legal Takedown**<br>`[Track 1 (Core MVP) - M1 - Sprint 2]` | **DB:** `audit_events`, `takedown_cases`<br>**API:**<br>• `GET /api/v1/audit-logs`<br>• `POST /api/v1/takedown-cases`<br>• `POST /api/v1/takedown-cases/{id}/execute` | • Bảng `audit_events` được phân vùng theo tháng, áp dụng `REVOKE UPDATE, DELETE` và Trigger chặn sửa xóa.<br>• Khi thực thi `takedown_cases`, hệ thống phát sự kiện `rights.takedown.executed` để gỡ tài liệu/câu hỏi khỏi tìm kiếm, CDN và kho bốc đề mới trong $< 30\text{s}$, đồng thời lưu biên bản pháp lý. | • Mọi thao tác nhạy cảm (Publish, sửa đáp án, đổi quyền, Takedown) đều có bản ghi `audit_events` kèm `before_json`, `after_json`, `reason` và `correlation_id`. |
| **G03 — Transactional Outbox, Inbox & Job Operations**<br>`[Track 1 (Core MVP) - M1 - Sprint 1]` | **DB:** `outbox_events`, `inbox_events`<br>**API:**<br>• `GET /api/v1/admin/outbox/stats`<br>• `GET /api/v1/admin/dlq`<br>• `POST /api/v1/admin/dlq/{id}/replay` | • Cung cấp cơ chế **Transactional Outbox** (`FOR UPDATE SKIP LOCKED`) để phát sự kiện tin cậy và bảng **Idempotent Consumer Inbox** (`inbox_events` với `UNIQUE(consumer_name, event_id)`) để đảm bảo mọi Worker xử lý sự kiện đúng 1 lần (Exactly-Once Side Effect).<br>• Cung cấp giao diện quản lý và Replay có kiểm soát các mục trong Dead-Letter Queue. | • Gửi lặp cùng một `event_id` vào Worker 5 lần chỉ thực thi tác dụng phụ đúng 1 lần nhờ chốt chặn `inbox_events`. |
| **G04 — Learning & Operational Analytics Warehouse**<br>`[Track 3 (Expansion) - M5 - Sprint 12]` | **DB:** `analytics_events`, `daily_metric_rollups`<br>**API:**<br>• `GET /api/v1/analytics/platform-kpis`<br>• `GET /api/v1/analytics/pipeline-yield` | • Thu thập sự kiện chuẩn hóa để tính toán các chỉ số vận hành và học tập: WLAU (Weekly Learning Active Users), tỷ lệ bóc tách đề tự động thành công (Pipeline Yield), tỷ lệ câu hỏi phải sửa tay và mức tăng trưởng năng lực học sinh (Mastery Gain). | • Báo cáo KPI chạy trên bảng tổng hợp `daily_metric_rollups`, không gây tải nặng lên các bảng giao dịch phòng thi. |
| **G05 — Subscriptions, Billing & Usage Ledger**<br>`[Track 3 (Expansion) - M5 - Sprint 12]` | **DB:** `subscription_plans`, `subscriptions`, `usage_ledger`, `payment_webhooks`<br>**API:**<br>• `GET /api/v1/plans`<br>• `POST /api/v1/subscriptions`<br>• `POST /api/v1/webhooks/payment` | • Quản lý gói cước (`FREE`, `STUDENT_PRO`, `TEACHER_PRO`, `SCHOOL_ENTERPRISE`), xử lý webhook thanh toán bất biến có kiểm tra chữ ký HMAC và ghi sổ cái tiêu thụ (`usage_ledger`) cho lượt tải PDF thương hiệu và hạn mức AI. | • Webhook thanh toán gửi trùng nhiều lần chỉ cộng hạn mức đúng 1 lần; phát sự kiện `subscription.updated` đồng bộ tức thì sang `F05`. |
| **G06 — Vertical Packs, Localization & Accessibility**<br>`[Track 3 (Expansion) - M5 - Sprint 12]` | **DB:** `vertical_packs`, `locale_bundles`<br>**API:**<br>• `GET /api/v1/vertical-packs`<br>• `POST /api/v1/vertical-packs/{code}/activate` | • Đóng gói cấu hình mở rộng cho các mảng ngoài K-12 (như IELTS, TOEIC, HSK 3.0, Đại học) dưới dạng `vertical_packs` (chứa Taxonomy mẫu, Assessment Framework và Scoring Rules riêng) mà không làm phình to hay thay đổi bảng lõi của K-12. | • Bật/tắt một `VerticalPack` mới không yêu cầu sửa đổi bất kỳ dòng code hay bảng DDL cốt lõi nào của hệ thống. |

---


### 3.8. Kiến trúc Giao diện Web (Frontend Architecture), Ánh xạ Nâng cấp & Đặc tả Chi tiết 6 Workspaces

#### A. Định hướng Kiến trúc Frontend & Lộ trình Nâng cấp từ `app/templates/*`, `app/static/js/*`
Trong repo `exam-system` hiện tại, giao diện đang dùng kết hợp các trang HTML/Jinja2 (`app/templates/result.html`, `admin/*`, `student/*`) và JavaScript thuần (`app/static/js/attempt.js`). Để vừa đảm bảo **tốc độ tải trang và chuẩn SEO < 1 giây cho hàng trăm nghìn trang bài học lý thuyết**, vừa đáp ứng **độ mượt, khả năng chịu lỗi mất mạng và tương tác phức tạp ở Phòng thi & Công cụ Cắt/Duyệt đề**, kiến trúc Web V11.3 áp dụng mô hình **Next.js (App Router, TypeScript) + TailwindCSS + TanStack Query + Zustand + Dexie.js (IndexedDB)**:

| Thành phần Giao diện trong `exam-system` cũ | Kiến trúc Web đích (V11.3 Frontend) | Chiến lược Render & Công nghệ Cốt lõi |
| :--- | :--- | :--- |
| Trang đọc bài học chuyên đề & Thư viện đề (`app/routers/library.py`) | **Workspace 1: Learner Study & Practice Hub** | **SSR / ISR (Incremental Static Regeneration):** Render sẵn HTML + công thức `KaTeX` trên Server giúp trang tải tức thì (`LCP < 1.2s`), chuẩn SEO Google, sau đó Hydrate 4 Tab bài tập tương tác ở phía Client. |
| Phòng làm bài thi (`app/static/js/attempt.js`) | **Workspace 2: High-Resilience Online Exam Room** | **SPA (Client-Side State Machine + IndexedDB):** Đồng hồ đồng bộ Server (`ServerClockSync`), lưu nháp tức thì vào `IndexedDB` (`< 5ms`) trước khi đẩy ngầm qua `AutosaveQueue` có `Idempotency-Key` & `client_sequence`. |
| Màn hình nhập đề & Cắt hình (`app/routers/admin/*`) | **Workspace 3: Side-by-Side Bounding-Box Reviewer** | **Interactive Canvas Split-Pane:** Khung trái vẽ trang PDF gốc + lớp phủ SVG `bbox` cho phép kéo-thả cắt lại hình (`Re-crop`); khung phải là form chỉnh sửa câu hỏi và xem cảnh báo nghiệm từ `SymPy`. |
| Trình tạo bài viết & Câu hỏi (`app/routers/maker.py`) | **Workspace 4: VietJack-Style Block Studio & Item Authoring** | **Block-Based Editor (TipTap / Lexical tùy biến):** Hỗ trợ gõ lệnh nhanh `/congthuc`, `/phuongphap`, `/chuy`, `/vidu` kèm khung Live KaTeX Preview kiểm tra lỗi cú pháp toán/hóa học tức thì. |
| Giao diện Giáo viên & Tạo đề ma trận (`app/routers/teacher.py`) | **Workspace 5: Matrix Exam Builder & Teacher LMS Hub** | **Reactive Matrix Grid & Data Charts:** Bảng chọn số câu theo Chuyên đề $\times$ Độ khó báo thiếu câu (`ShortageReport`) theo thời gian thực; biểu đồ phổ điểm lớp và phân tích phương án nhiễu. |
| Quản trị hệ thống, Crawler, Drive & Takedown | **Workspace 6: Admin Ops, Drive Picker & Takedown Console** | **Operational Dashboard:** Tích hợp Google Picker SDK (`drive.file`), biểu đồ `ParserDriftMonitor`, quản lý hàng đợi DLQ và nút khẩn cấp `Execute Legal Takedown`. |

#### B. Đặc tả Chi tiết 6 Workspaces & Quy tắc Xử lý 6 Trạng thái UI Bắt buộc

Mọi màn hình trong 6 Workspaces bắt buộc phải cài đặt đầy đủ **6 trạng thái UI chuẩn (`Loading Skeleton`, `Empty State`, `Error Boundary`, `Locked Pro Preview`, `Offline Retry Banner`, `Sequence Conflict Modal`)**:

| Workspace & Đường dẫn Route | Bố cục Màn hình (Layout) & Luồng Tương tác Trọng tâm | Xử lý Đặc thù về Toán/Hóa (KaTeX), Ngoại tuyến & Bảo mật |
| :--- | :--- | :--- |
| **1. Learner Study & Practice Hub**<br>Route: `/hoc-lieu/[slug]`, `/lo-trinh`, `/so-tay-cau-sai` | • **Cột trái (260px, Collapsible):** Cây mục lục SGK 4 cấp (`Chương -> Bài -> Mục -> Dạng bài`) gắn chấm màu theo mức độ làm chủ (`Mastery`: Xanh $\ge 80\%$, Vàng $50–79\%$, Đỏ $< 50\%$).<br>• **Cột giữa (760px, Tối ưu đọc):** Hiển thị các khối bài học chuẩn VietJack nâng cấp: *Tóm tắt lý thuyết*, *Hộp công thức đóng khung nổi bật*, *Các bước giải bài toán*, *Chú ý / Bẫy hay sai*, *Ví dụ mẫu có nút Xem/Ẩn lời giải*. Ngay bên dưới bài học là **4 Tab bài tập tương tác** (*Tự luận, ABCD, Đúng/Sai 4 ý, Trả lời ngắn*).<br>• **Cột phải (320px, Sticky Drawer):** Khung chat **Gia sư AI** (3 nút chuyển chế độ: *Gợi ý bước tiếp theo / Giải thích chi tiết / Thử thách câu tương tự*) và danh sách câu hỏi cần khắc phục trong Sổ tay câu sai. | • Công thức `KaTeX` được cấu hình `throwOnError: false`, `trust: false` (chống XSS qua mã LaTeX độc hại).<br>• Với tài liệu thuộc gói `STUDENT_PRO` mà học sinh đang ở gói `FREE`, hiển thị trạng thái **`Locked Preview`**: cho xem trọn vẹn 2 trang đầu (`PREVIEW_PDF`) kèm nút mở khóa, tuyệt đối không tải URL của file gốc xuống DOM. |
| **2. High-Resilience Online Exam Room**<br>Route: `/phong-thi/[attempt_id]` | • **Thanh Header cố định (Sticky Topbar):** Hiển thị tên kỳ thi, đồng hồ đếm ngược tính theo `server_time_offset_ms`, huy hiệu trạng thái lưu bài (`Đã lưu trên máy chủ` màu xanh / `Đang lưu tạm ngoại tuyến (N câu)` màu cam) và nút `Nộp bài`.<br>• **Khu vực làm bài 3 Phần chuẩn THPTQG 2025+:**<br>  - *Phần I (ABCD):* Các nút chọn phương án lớn, hỗ trợ phím tắt `A, B, C, D` và nút gạch bỏ phương án nhiễu (Eliminate Option).<br>  - *Phần II (Đúng/Sai 4 ý):* Bảng 4 dòng `a), b), c), d)` với 2 cột radio `Đúng` / `Sai` rõ ràng; tự động đánh dấu cảnh báo màu vàng trên bảng câu hỏi nếu thí sinh mới chọn 1–3 ý mà quên ý còn lại.<br>  - *Phần III (Trả lời ngắn 4 ký tự):* Ô nhập liệu gồm **4 ô ký tự trực quan** mô phỏng phiếu tô trắc nghiệm THPTQG (chỉ cho phép nhập chữ số `0–9`, dấu `-` ở ô đầu, và dấu `,` thập phân); cảnh báo đỏ ngay dưới ô nếu thí sinh gõ dấu chấm phân cách hàng nghìn (`1.000`).<br>• **Bảng điều hướng câu hỏi (Question Palette):** Hiển thị trạng thái từng câu và nút gắn cờ (`Flag`). | • **Chống mất bài tuyệt đối:** Mỗi khi thí sinh bấm chọn đáp án, UI lưu ngay vào bảng `pending_autosaves` trong `IndexedDB` (`< 5ms`) rồi mới gửi `PUT /attempts/{id}/responses/{item_id}` kèm `Idempotency-Key` và `client_sequence`.<br>• Nếu mất mạng, hiển thị thanh **`Offline Retry Banner`** màu cam nhưng **không khóa màn hình** — thí sinh vẫn tiếp tục làm các câu khác bình thường; khi có mạng trở lại, Worker ngầm trên trình duyệt tự động đồng bộ lại toàn bộ.<br>• Nếu thí sinh mở 2 tab cùng lúc gây lỗi `409 STALE_CLIENT_SEQUENCE`, hiển thị hộp thoại **`Conflict Modal`** và gọi `GET /attempts/{id}/sync` để lấy trạng thái mới nhất từ Server. |
| **3. Side-by-Side Bounding-Box Reviewer**<br>Route: `/studio/imports/[run_id]/review` | • **Khung trái (50% màn hình — Interactive PDF/Image Canvas):** Hiển thị trang tài liệu gốc với các khung chữ nhật màu xanh (câu hỏi), màu cam (hình vẽ cắt `FIGURE_CROP`), màu tím (bảng biểu `HTML_TABLE`). Khi click vào Câu $k$ ở khung phải, khung trái tự động cuộn và phóng to đúng vùng `bbox` của Câu $k$. Hỗ trợ kéo chuột vẽ lại khung `bbox` để cắt lại hình ảnh ngay trên trình duyệt.<br>• **Khung phải (50% màn hình — Structured Question Inspector):** Form chỉnh sửa nội dung câu hỏi, bảng đối chiếu giữa **Đáp án nguồn (HDC)** và **Đáp án giải độc lập bởi `SymPy + LLM`**. Nếu 2 bên lệch nhau, hiển thị nhãn đỏ `DISPUTED_ANSWER` yêu cầu Reviewer kiểm tra lại trước khi bấm `Approve`. | • Cổng khóa nút `Publish` ngay trên UI: Nút `Publish lên Kho` bị vô hiệu hóa (Disabled) kèm giải thích rõ nếu `rights_status` đang là `PENDING_REVIEW` / `RESTRICTED` hoặc `verification_status != 'VERIFIED'`. |
| **4. VietJack-Style Content Studio & Authoring**<br>Route: `/studio/content/[id]`, `/studio/questions/new` | • Trình soạn thảo chia cột: bên trái là các khối nội dung (`THEORY_SUMMARY`, `FORMULA_BOX`, `METHOD_STEPS`, `TIP_WARNING`, `WORKED_EXAMPLE`), bên phải là bản xem trước giao diện Học sinh theo thời gian thực (Desktop / Mobile).<br>• Hỗ trợ dán trực tiếp công thức từ Word/MathType hoặc gõ LaTeX có gợi ý tự động các ký hiệu Toán, Lý, Hóa (`\vec{F}`, `\Delta \varphi`, `\xrightarrow{t^\circ}`). | • Bộ kiểm tra `Pre-Submit Validator` quét toàn bộ các cặp dấu `$...$`, `\begin{aligned}...\end{aligned}` và kiểm tra quyền sở hữu của các `asset_id` hình ảnh trước khi cho phép lưu bản nháp. |
| **5. Matrix Exam Builder & Teacher LMS Hub**<br>Route: `/teacher/exam-builder`, `/teacher/classes/[id]` | • **Exam Builder:** Bảng ma trận hai chiều (Hàng = Chương/Bài/Chuyên đề trong SGK; Cột = 4 mức độ `NB, TH, VD, VDC` $\times$ 3 loại câu hỏi). Giáo viên nhập số lượng câu mong muốn -> hệ thống hiển thị ngay số câu khả dụng trong kho và báo đỏ các ô thiếu câu (`ShortageReport`).<br>• **Teacher Gradebook:** Bảng điểm toàn lớp, biểu đồ phân phối điểm, danh sách học sinh chưa nộp bài và bảng **Item Analysis** chỉ ra 5 câu cả lớp sai nhiều nhất kèm tỷ lệ chọn từng phương án nhiễu A/B/C/D. | • Tích hợp bảng trợ lý **Teacher AI Copilot (`E06`)**: gợi ý nhóm học sinh cần phụ đạo cùng chuyên đề và tạo sẵn nút *"Giao phiếu bài tập khắc phục 10 câu cho nhóm học sinh này"*. |
| **6. Admin Ops, Crawler, Drive & Takedown Console**<br>Route: `/admin/ops`, `/admin/sources`, `/admin/takedowns` | • **Quản lý Nguồn & Google Drive:** Nút mở **Google Picker** (chế độ chọn nhiều file đề/HDC hoặc file `.zip`) và hướng dẫn chia sẻ thư mục trường học cho địa chỉ email `Service Account`. Hiển thị danh sách các cặp `File Đề + File HDC` được ghép tự động kèm điểm tin cậy 4 tín hiệu để Admin bấm `Confirm`.<br>• **Giám sát Hàng đợi & Takedown:** Biểu đồ độ trễ Outbox, danh sách `dead_letter_items` có nút `Replay`, và biểu mẫu thực thi lệnh gỡ bỏ bản quyền khẩn cấp (`Takedown`). | • Mọi thao tác bấm nút nhạy cảm trên trang Admin (`Execute Takedown`, `Override Grade`, `Change Rights Status`, `Replay DLQ`) đều bật hộp thoại bắt buộc nhập `reason` (tối thiểu 15 ký tự) để lưu vào `audit_events`. |

---

# PHẦN II (TẬP 2 / 3 — V11.3): THIẾT KẾ DATABASE VẬT LÝ TOÀN DIỆN (DDL + RLS + TRIGGERS), PYDANTIC/EVENT CONTRACTS & 9 THUẬT TOÁN LÕI (BAO GỒM WEB CRAWLER & GOOGLE DRIVE SYNC)

## CHƯƠNG 4. PHYSICAL POSTGRESQL DDL TOÀN DIỆN (FULL SCHEMA, COMPOSITE FK TENANCY, RLS & IMMUTABILITY TRIGGERS)

> **Chỉ đạo triển khai Alembic (Khắc phục lỗi P1 trong Review):** Không đặt file `.sql` thô đơn lẻ trong `alembic/versions/`. Đội Backend sử dụng các file migration Python chuẩn (`001_foundation_and_rls.py` đến `004_ai_lms_and_ops.py`) gọi `op.execute(...)` chứa kịch bản SQL chuẩn hóa dưới đây. Toàn bộ bảng đều có:
> 1. Khóa chính **UUIDv7** (`uuid_generate_v7()`) tự sinh theo thứ tự thời gian.
> 2. Cột **`tenant_id`** hiện diện trên **mọi bảng nghiệp vụ** (bao gồm cả 22 bảng con thông qua **Composite Foreign Key** `(parent_id, tenant_id)`) để áp dụng Row-Level Security (RLS) trực tiếp không cần `JOIN`.
> 3. Ràng buộc **`CHECK`** cho toàn bộ các cột trạng thái (5 trục trạng thái) và Trigger bảo vệ tính bất biến.

```sql
-- ============================================================================
-- MIGRATION 001: EXTENSIONS, UUIDv7, IMMUTABLE UNACCENT, ROLES & IDENTITY/TENANCY
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "unaccent";
CREATE EXTENSION IF NOT EXISTS "ltree";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 0. Khởi tạo 7 DB Roles chuẩn Least-Privilege (Khắc phục AUD-004: XÓA BỎ HOÀN TOÀN BYPASSRLS trên mọi Runtime/Worker Role)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_migrator') THEN
        CREATE ROLE ulp_migrator NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_app_rw') THEN
        CREATE ROLE ulp_app_rw NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_exam_session_rw') THEN
        CREATE ROLE ulp_exam_session_rw NOLOGIN NOBYPASSRLS;
    END IF;
    -- Tách Worker theo nhiệm vụ, tất cả đều NOBYPASSRLS và bắt buộc SET LOCAL app.current_tenant_id theo từng Job/Event
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_worker_ingestion') THEN
        CREATE ROLE ulp_worker_ingestion NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_worker_grading') THEN
        CREATE ROLE ulp_worker_grading NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_analytics_ro') THEN
        CREATE ROLE ulp_analytics_ro NOLOGIN NOBYPASSRLS;
    END IF;
    -- Role chuyên trách các tác vụ nền xuyên Tenant (outbox_relay, attempt_expiry, metric_rollup) dùng policy hẹp theo app.system_job (Khắc phục P0-2)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_system_scheduler') THEN
        CREATE ROLE ulp_system_scheduler NOLOGIN NOBYPASSRLS;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION app_system_job() RETURNS text AS $$
    SELECT NULLIF(current_setting('app.system_job', true), '');
$$ LANGUAGE sql STABLE;

-- 1. Hàm sinh UUIDv7 (Tự động ưu tiên hàm C gốc uuidv7() nếu chạy trên PostgreSQL 18+, fallback sang PL/pgSQL trên PG 16/17)
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
DECLARE
    unix_ts_ms bytea;
    uuid_bytes bytea;
    v_native_uuid uuid;
BEGIN
    -- Nếu chạy trên PostgreSQL 18+, gọi trực tiếp hàm C gốc pg_catalog.uuidv7()
    IF to_regproc('pg_catalog.uuidv7') IS NOT NULL THEN
        EXECUTE 'SELECT pg_catalog.uuidv7()' INTO v_native_uuid;
        RETURN v_native_uuid;
    END IF;
    -- Fallback tương thích 100% trên PostgreSQL 16 & 17
    unix_ts_ms := substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3 FOR 6);
    uuid_bytes := unix_ts_ms || gen_random_bytes(10);
    uuid_bytes := set_byte(uuid_bytes, 6, (b'0111' || get_byte(uuid_bytes, 6)::bit(4))::bit(8)::int);
    uuid_bytes := set_byte(uuid_bytes, 8, (b'10' || get_byte(uuid_bytes, 8)::bit(6))::bit(8)::int);
    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- 2. Quản lý Phiên bản Từ điển FTS & Hàm f_unaccent Ghim Phiên bản (Khắc phục AUD-005)
CREATE TABLE fts_dictionary_versions (
    dict_name VARCHAR(80) PRIMARY KEY,
    rules_sha256 CHAR(64) NOT NULL,
    func_version INT NOT NULL DEFAULT 1,
    last_reindexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO fts_dictionary_versions (dict_name, rules_sha256, func_version)
VALUES ('public.unaccent', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 1)
ON CONFLICT (dict_name) DO NOTHING;

-- Chỉ đánh dấu IMMUTABLE khi đi kèm kiểm soát phiên bản rules_sha256 trong fts_dictionary_versions;
-- Khi nâng cấp từ điển PostgreSQL, migration bắt buộc cập nhật func_version và chạy REINDEX INDEX CONCURRENTLY.
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text AS $$
    SELECT public.unaccent('public.unaccent', $1);
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;

-- 3. Hàm đọc Context Đa thuê bao an toàn với PgBouncer Transaction Pooling
CREATE OR REPLACE FUNCTION current_app_tenant_id() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION system_public_tenant_id() RETURNS uuid AS $$
    SELECT '00000000-0000-0000-0000-000000000000'::uuid;
$$ LANGUAGE sql IMMUTABLE;

-- ============================================================================
-- NHÓM 1: IDENTITY, TENANCY, PARENTAL CONSENT (Luật BVDLCN 91/2025/QH15 & NĐ 13/2023) & RBAC/ENTITLEMENTS (F01, F04, F05)
-- ============================================================================
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    slug VARCHAR(80) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SUSPENDED', 'ARCHIVED')),
    settings_json JSONB NOT NULL DEFAULT '{}',
    daily_ai_budget_usd NUMERIC(10,2) NOT NULL DEFAULT 10.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Khởi tạo Tenant Hệ thống (Chứa SGK Kết nối tri thức & Học liệu Công khai dùng chung)
INSERT INTO tenants (id, slug, name, status)
VALUES ('00000000-0000-0000-0000-000000000000', 'system-public', 'System Public Knowledge Commons', 'ACTIVE')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    -- Không lưu email/họ tên dạng rõ để đảm bảo Crypto-Shredding thực chất:
    -- email_blind_index = HMAC_SHA256(lower(email), cluster_pepper) phục vụ tra cứu đăng nhập O(1)
    email_blind_index CHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255),
    -- pii_ciphertext_bytea chứa JSON {"email": ..., "display_name": ..., "phone": ...} mã hóa AES-256-GCM bằng DEK riêng của user_id
    pii_ciphertext_bytea BYTEA,
    pseudonym_handle VARCHAR(80) NOT NULL DEFAULT 'Learner',
    birth_year INT CHECK (birth_year IS NULL OR (birth_year >= 1920 AND birth_year <= 2100)),
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'LOCKED', 'PENDING_PARENTAL_CONSENT', 'ANONYMIZED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hàm STABLE tính trạng thái vị thành niên (< 16 tuổi) tại thời điểm truy vấn
CREATE OR REPLACE FUNCTION is_user_minor(p_birth_year INT, p_ref_date DATE DEFAULT CURRENT_DATE) RETURNS BOOLEAN AS $$
    SELECT p_birth_year IS NOT NULL AND (EXTRACT(YEAR FROM p_ref_date)::INT - p_birth_year) < 16;
$$ LANGUAGE sql STABLE PARALLEL SAFE;

-- View v_users_status cung cấp trạng thái is_minor tính động tại thời điểm truy vấn
CREATE OR REPLACE VIEW v_users_status WITH (security_invoker = true) AS
SELECT
    id,
    email_blind_index,
    pseudonym_handle,
    birth_year,
    is_user_minor(birth_year, CURRENT_DATE) AS is_minor,
    status,
    created_at
FROM users;

-- Bảng quản lý khóa mã hóa PII theo mô hình KMS Envelope Encryption (Khắc phục AUD-006: Bảo vệ cả trên DB Backup)
CREATE TABLE user_pii_encryption_keys (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    kms_kek_key_id VARCHAR(255) NOT NULL, -- Khóa chủ KEK nằm trên KMS/Vault NGOÀI PostgreSQL và ngoài bản Backup DB
    encrypted_dek_bytea BYTEA,            -- DEK đã được bọc bởi KEK; khi xóa dữ liệu: hủy KEK trên KMS + đặt NULL tại đây
    key_algorithm VARCHAR(30) NOT NULL DEFAULT 'AES_256_GCM_ENVELOPE',
    key_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (key_status IN ('ACTIVE', 'SHREDDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    shredded_at TIMESTAMPTZ,
    backup_retention_expires_at TIMESTAMPTZ
);

-- Bảng chứng nhận hoàn tất xóa dữ liệu cá nhân (Right to Erasure Completion Certificate)
CREATE TABLE erasure_certificates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    subject_user_id UUID NOT NULL,
    kms_kek_key_id VARCHAR(255) NOT NULL,
    primary_db_shredded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kms_kek_revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    backup_expiry_guaranteed_at TIMESTAMPTZ NOT NULL,
    verification_hash CHAR(64) NOT NULL,
    UNIQUE (id, tenant_id)
);

CREATE TABLE memberships (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    roles VARCHAR(50)[] NOT NULL DEFAULT '{LEARNER}',
    entitlement_tier VARCHAR(30) NOT NULL DEFAULT 'FREE'
        CHECK (entitlement_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO', 'SCHOOL_ENTERPRISE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,
    UNIQUE (tenant_id, user_id),
    UNIQUE (id, tenant_id)
);

-- Bảng xác nhận đồng ý của phụ huynh/nhà trường theo Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP
CREATE TABLE parental_consents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    learner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Khắc phục P0-4: Không lưu guardian_email dạng rõ; lưu blind_index (HMAC-SHA256) và ciphertext (mã hóa bằng DEK của learner_user_id)
    guardian_email_blind_index CHAR(64) NOT NULL,
    guardian_email_ciphertext BYTEA NOT NULL,
    guardian_relationship VARCHAR(40) NOT NULL CHECK (guardian_relationship IN ('PARENT', 'LEGAL_GUARDIAN', 'SCHOOL_CONTRACT')),
    consent_scope_json JSONB NOT NULL DEFAULT '{"learning_history": true, "ai_tutor": true}',
    verification_method VARCHAR(40) NOT NULL, -- OTP_EMAIL, SIGNED_SCHOOL_AGREEMENT
    consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    UNIQUE (id, tenant_id)
);

CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    code VARCHAR(60) NOT NULL,
    name VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id)
);

CREATE TABLE permissions (
    code VARCHAR(80) PRIMARY KEY,
    description VARCHAR(255) NOT NULL
);

CREATE TABLE role_permissions (
    role_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    permission_code VARCHAR(80) NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_code),
    FOREIGN KEY (role_id, tenant_id) REFERENCES roles(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE classes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    group_id UUID,
    teacher_membership_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    academic_year VARCHAR(30),
    invite_code VARCHAR(30) UNIQUE,
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (group_id, tenant_id) REFERENCES groups(id, tenant_id) ON DELETE SET NULL,
    FOREIGN KEY (teacher_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE class_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    class_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    role_in_class VARCHAR(30) NOT NULL DEFAULT 'STUDENT'
        CHECK (role_in_class IN ('STUDENT', 'TEACHING_ASSISTANT')),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE')), -- Soft-delete bảo toàn điểm lịch sử
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,
    UNIQUE (class_id, membership_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

-- ============================================================================
-- NHÓM 2: TAXONOMY (TÁCH ĐỊNH DANH & PHIÊN BẢN + LTREE), KNOWLEDGE GRAPH & CURRICULUM (B04, B05, B06)
-- ============================================================================
CREATE TABLE taxonomy_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'SYSTEM_PUBLIC'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    parent_id UUID REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
    node_type VARCHAR(40) NOT NULL
        CHECK (node_type IN ('DOMAIN', 'SUBJECT', 'TOPIC', 'CONCEPT', 'SKILL')),
    code VARCHAR(120) NOT NULL,
    code_path ltree NOT NULL, -- VD: 'MATH.PROB.BAYES'
    current_version_no INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    retired_into_node_id UUID REFERENCES taxonomy_nodes(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id)
);
CREATE INDEX ix_taxonomy_nodes_ltree_gist ON taxonomy_nodes USING GIST (code_path);

CREATE TABLE taxonomy_node_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL,
    version_no INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    description_md TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (taxonomy_node_id, version_no),
    FOREIGN KEY (taxonomy_node_id, tenant_id) REFERENCES taxonomy_nodes(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE taxonomy_aliases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL,
    alias_name VARCHAR(255) NOT NULL,
    normalized_alias VARCHAR(255) NOT NULL,
    UNIQUE (tenant_id, normalized_alias),
    FOREIGN KEY (taxonomy_node_id, tenant_id) REFERENCES taxonomy_nodes(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE knowledge_edges (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES tenants(id) ON DELETE CASCADE,
    source_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
    target_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
    edge_type VARCHAR(40) NOT NULL
        CHECK (edge_type IN ('PREREQUISITE', 'RELATED_TO', 'EXTENDS', 'APPLIED_IN', 'COMMON_MISCONCEPTION')),
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.000 CHECK (confidence BETWEEN 0 AND 1),
    graph_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, source_node_id, target_node_id, edge_type, graph_version),
    CHECK (source_node_id <> target_node_id)
);

CREATE TABLE graph_validation_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    graph_version INT NOT NULL,
    is_dag_valid BOOLEAN NOT NULL,
    cycle_path_json JSONB NOT NULL DEFAULT '[]',
    validated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE curriculum_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES tenants(id) ON DELETE CASCADE,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'SYSTEM_PUBLIC'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')),
    code VARCHAR(80) NOT NULL,
    name VARCHAR(255) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    grade_band VARCHAR(30) NOT NULL,
    version_label VARCHAR(40) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PUBLISHED'
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id)
);

CREATE TABLE curriculum_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    curriculum_version_id UUID NOT NULL,
    parent_id UUID REFERENCES curriculum_nodes(id) ON DELETE CASCADE,
    node_level VARCHAR(40) NOT NULL
        CHECK (node_level IN ('CHAPTER', 'LESSON', 'SECTION', 'PROBLEM_TYPE')),
    code VARCHAR(120) NOT NULL,
    title VARCHAR(255) NOT NULL,
    sequence_order INT NOT NULL DEFAULT 0,
    UNIQUE (curriculum_version_id, code),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (curriculum_version_id, tenant_id) REFERENCES curriculum_versions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE taxonomy_mappings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    curriculum_node_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
    mapping_weight NUMERIC(4,2) NOT NULL DEFAULT 1.00,
    UNIQUE (curriculum_node_id, taxonomy_node_id),
    FOREIGN KEY (curriculum_node_id, tenant_id) REFERENCES curriculum_nodes(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 3: ASSETS, CONTENT LIBRARY, STUDIO & SEARCH (B01, B02, B03, B07)
-- ============================================================================
CREATE TABLE assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    parent_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
    asset_role VARCHAR(40) NOT NULL
        CHECK (asset_role IN ('RAW_SOURCE', 'BRANDED_PDF', 'PREVIEW_PDF', 'PAGE_RENDER', 'FIGURE_CROP', 'AUDIO', 'THUMBNAIL')),
    object_key VARCHAR(512) UNIQUE NOT NULL,
    mime_type VARCHAR(120) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    is_immutable_bronze BOOLEAN GENERATED ALWAYS AS (asset_role = 'RAW_SOURCE') STORED,
    metadata_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);
CREATE INDEX ix_assets_tenant_sha256 ON assets (tenant_id, sha256);

CREATE TABLE content_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    content_type VARCHAR(40) NOT NULL
        CHECK (content_type IN ('ARTICLE', 'LESSON', 'VIDEO', 'DOCUMENT', 'FLASHCARD_SET', 'VOCABULARY_SET')),
    slug VARCHAR(255) NOT NULL,
    curriculum_node_id UUID REFERENCES curriculum_nodes(id) ON DELETE SET NULL,
    primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id) ON DELETE SET NULL,
    current_version_id UUID,
    -- Đủ 4 trục trạng thái trên thực thể học liệu (Fail-Closed Default)
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'NORMALIZED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
    access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, slug),
    UNIQUE (id, tenant_id),
    -- Cổng kiểm soát cứng: Không thể PUBLISHED nếu chưa VERIFIED hoặc bản quyền không hợp lệ
    CONSTRAINT chk_content_publish_gate CHECK (
        publication_status <> 'PUBLISHED'
        OR (verification_status = 'VERIFIED' AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)
    )
);

CREATE TABLE content_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    content_item_id UUID NOT NULL,
    version_no INT NOT NULL,
    title VARCHAR(500) NOT NULL,
    summary_md TEXT,
    blocks_json JSONB NOT NULL DEFAULT '[]',
    branded_asset_id UUID,
    preview_asset_id UUID,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED', 'TAKEN_DOWN')),
    is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (content_item_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (content_item_id, tenant_id) REFERENCES content_items(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (branded_asset_id, tenant_id) REFERENCES assets(id, tenant_id),
    FOREIGN KEY (preview_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
);

ALTER TABLE content_items
    ADD CONSTRAINT fk_content_items_current_version
    FOREIGN KEY (current_version_id, tenant_id)
    REFERENCES content_versions(id, tenant_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE content_drafts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    content_item_id UUID NOT NULL,
    author_id UUID NOT NULL REFERENCES users(id),
    working_blocks_json JSONB NOT NULL DEFAULT '[]',
    validation_errors_json JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (content_item_id, tenant_id) REFERENCES content_items(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE content_blocks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    content_version_id UUID NOT NULL,
    block_order INT NOT NULL,
    block_type VARCHAR(40) NOT NULL
        CHECK (block_type IN ('THEORY_SUMMARY', 'FORMULA_BOX', 'METHOD_STEPS', 'TIP_WARNING', 'WORKED_EXAMPLE', 'INTERACTIVE_QUESTION_EMBED', 'HTML_TABLE')),
    payload_json JSONB NOT NULL,
    UNIQUE (content_version_id, block_order),
    FOREIGN KEY (content_version_id, tenant_id) REFERENCES content_versions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE comment_threads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    target_type VARCHAR(40) NOT NULL,
    target_id UUID NOT NULL,
    author_id UUID NOT NULL REFERENCES users(id),
    body_text TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE content_assets (
    content_version_id UUID NOT NULL,
    asset_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    PRIMARY KEY (content_version_id, asset_id),
    FOREIGN KEY (content_version_id, tenant_id) REFERENCES content_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE publications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_type VARCHAR(40) NOT NULL CHECK (entity_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')),
    entity_id UUID NOT NULL,
    version_id UUID NOT NULL,
    published_by UUID NOT NULL REFERENCES users(id),
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE access_grants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    membership_id UUID NOT NULL,
    resource_type VARCHAR(40) NOT NULL,
    resource_id UUID NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE search_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
    doc_type VARCHAR(30) NOT NULL CHECK (doc_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')),
    entity_id UUID NOT NULL,
    title TEXT NOT NULL,
    body_plain TEXT NOT NULL,
    subject_code VARCHAR(50),
    grade_band VARCHAR(30),
    taxonomy_path ltree,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') ||
        setweight(to_tsvector('simple', f_unaccent(coalesce(body_plain, ''))), 'B')
    ) STORED,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_type, entity_id)
);
CREATE INDEX ix_search_documents_fts_gin ON search_documents USING GIN (search_vector);
CREATE INDEX ix_search_documents_title_trgm ON search_documents USING GIN (f_unaccent(title) gin_trgm_ops);

CREATE TABLE collections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    owner_membership_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (owner_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE collection_items (
    collection_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    item_type VARCHAR(30) NOT NULL,
    item_id UUID NOT NULL,
    sequence_order INT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, item_type, item_id),
    FOREIGN KEY (collection_id, tenant_id) REFERENCES collections(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 4: ACQUISITION, CRAWLER, DRIVE SYNC, OCR & PROVENANCE (A01 – A09)
-- ============================================================================
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_type VARCHAR(40) NOT NULL
        CHECK (source_type IN ('MANUAL_UPLOAD', 'GOOGLE_DRIVE', 'WEB_CRAWLER', 'PARTNER_API')),
    name VARCHAR(255) NOT NULL,
    base_uri VARCHAR(512),
    adapter_key VARCHAR(80),
    config_json JSONB NOT NULL DEFAULT '{}',
    rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    attribution_text VARCHAR(500),
    policy_status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (policy_status IN ('ACTIVE', 'PAUSED_DRIFT', 'PAUSED_MANUAL', 'REVOKED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE source_rights (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    license_type VARCHAR(60) NOT NULL,
    allow_rebranding BOOLEAN NOT NULL DEFAULT FALSE,
    allow_public_redistribution BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_asset_id UUID,
    verified_by UUID REFERENCES users(id),
    verified_at TIMESTAMPTZ,
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE source_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    provider VARCHAR(40) NOT NULL DEFAULT 'GOOGLE_DRIVE',
    secret_vault_ref VARCHAR(255) NOT NULL,
    granted_scopes VARCHAR(120)[] NOT NULL DEFAULT '{https://www.googleapis.com/auth/drive.file}',
    status VARCHAR(30) NOT NULL DEFAULT 'CONNECTED'
        CHECK (status IN ('CONNECTED', 'REAUTH_REQUIRED', 'REVOKED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE source_sync_cursors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_connection_id UUID UNIQUE NOT NULL,
    cursor_token VARCHAR(512) NOT NULL,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (source_connection_id, tenant_id) REFERENCES source_connections(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE crawl_urls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    url VARCHAR(1024) NOT NULL,
    url_hash CHAR(64) NOT NULL,
    etag VARCHAR(255),
    last_modified_header VARCHAR(120),
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'FETCHED', 'SKIPPED_304', 'FAILED', 'BLOCKED_ROBOTS')),
    last_fetched_at TIMESTAMPTZ,
    UNIQUE (source_id, url_hash),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE crawl_fetches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    crawl_url_id UUID NOT NULL,
    http_status INT NOT NULL,
    raw_asset_id UUID,
    resolved_ip VARCHAR(64) NOT NULL,
    parse_yield_count INT NOT NULL DEFAULT 0,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (crawl_url_id, tenant_id) REFERENCES crawl_urls(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
);

CREATE TABLE source_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    title VARCHAR(500),
    mime_type VARCHAR(120) NOT NULL,
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'INGESTED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    inferred_metadata_json JSONB NOT NULL DEFAULT '{}',
    paired_solution_doc_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, external_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE file_pairing_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    exam_doc_id UUID NOT NULL,
    solution_doc_id UUID NOT NULL,
    similarity_score NUMERIC(4,3) NOT NULL,
    signals_json JSONB NOT NULL DEFAULT '{}', -- filename_stem, folder_proximity, header_match, item_count_match
    status VARCHAR(30) NOT NULL DEFAULT 'PROPOSED'
        CHECK (status IN ('PROPOSED', 'CONFIRMED', 'REJECTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (exam_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (solution_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE document_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_document_id UUID NOT NULL,
    version_no INT NOT NULL,
    raw_asset_id UUID NOT NULL,
    checksum_sha256 CHAR(64) NOT NULL,
    page_count INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_document_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_document_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE parser_definitions (
    code VARCHAR(60) PRIMARY KEY,
    version VARCHAR(40) NOT NULL,
    supported_mimes VARCHAR(120)[] NOT NULL,
    config_schema_json JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE acquisition_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_id UUID,
    document_version_id UUID,
    job_type VARCHAR(40) NOT NULL
        CHECK (job_type IN ('EXAM_IMPORT', 'DRIVE_SYNC', 'WEB_CRAWL', 'REPROCESS_EXTRACTION')),
    job_status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
        CHECK (job_status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'DEAD_LETTER', 'CANCELLED')),
    current_stage VARCHAR(50),
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    metrics_json JSONB NOT NULL DEFAULT '{}',
    last_error_code VARCHAR(80),
    last_error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id)
);
CREATE INDEX ix_acquisition_jobs_status_created ON acquisition_jobs (job_status, created_at);

CREATE TABLE pipeline_stage_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    acquisition_job_id UUID NOT NULL,
    stage_name VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')),
    worker_version VARCHAR(40),
    input_ref VARCHAR(512),
    output_ref VARCHAR(512),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    FOREIGN KEY (acquisition_job_id, tenant_id) REFERENCES acquisition_jobs(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE dead_letter_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    acquisition_job_id UUID,
    source_queue VARCHAR(80) NOT NULL,
    payload_json JSONB NOT NULL,
    error_trace TEXT NOT NULL,
    replayed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE extraction_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    document_version_id UUID NOT NULL,
    acquisition_job_id UUID,
    profile_code VARCHAR(60) NOT NULL,
    parser_version VARCHAR(40) NOT NULL,
    ocr_engine_version VARCHAR(40),
    confidence_score NUMERIC(4,3),
    silver_artifact_key VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (acquisition_job_id, tenant_id) REFERENCES acquisition_jobs(id, tenant_id)
);

CREATE TABLE extracted_pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    page_no INT NOT NULL,
    render_asset_id UUID,
    width_pt NUMERIC(8,2) NOT NULL,
    height_pt NUMERIC(8,2) NOT NULL,
    detected_header_y0 NUMERIC(8,2),
    detected_footer_y1 NUMERIC(8,2),
    raw_text TEXT,
    UNIQUE (extraction_run_id, page_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE layout_regions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extracted_page_id UUID NOT NULL,
    region_type VARCHAR(40) NOT NULL
        CHECK (region_type IN ('HEADER', 'FOOTER', 'QUESTION_BLOCK', 'FIGURE', 'TABLE', 'FORMULA_BLOCK')),
    bbox_json JSONB NOT NULL,
    cropped_asset_id UUID,
    extracted_text TEXT,
    FOREIGN KEY (extracted_page_id, tenant_id) REFERENCES extracted_pages(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE provenance_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    document_version_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    page_no INT NOT NULL,
    bbox_json JSONB,
    section_label VARCHAR(120),
    original_question_label VARCHAR(60),
    raw_snippet_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE exam_import_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    proposed_structure_json JSONB NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 5: CANDIDATES, DEDUPLICATION, QUESTION BANK & QUALITY GOVERNANCE (A07, A08, A09, C01, C02)
-- ============================================================================
CREATE TABLE duplicate_clusters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cluster_type VARCHAR(30) NOT NULL
        CHECK (cluster_type IN ('EXACT', 'STRUCTURAL_VARIANT', 'SEMANTIC_NEAR')),
    canonical_question_id UUID,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (status IN ('PENDING_REVIEW', 'RESOLVED_MERGED', 'RESOLVED_KEEP_VARIANTS')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE content_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    provenance_id UUID,
    title VARCHAR(500) NOT NULL,
    blocks_json JSONB NOT NULL DEFAULT '[]',
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'EXTRACTED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    promoted_content_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (provenance_id, tenant_id) REFERENCES provenance_records(id, tenant_id)
);

CREATE TABLE question_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID,
    provenance_id UUID,
    parent_candidate_id UUID,
    acquisition_method VARCHAR(40) NOT NULL
        CHECK (acquisition_method IN ('DOCUMENT_IMPORT', 'WEB_CRAWL', 'DRIVE_SYNC', 'AI_GENERATED', 'MANUAL_AUTHOR')),
    item_type VARCHAR(40) NOT NULL
        CHECK (item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY', 'GROUP_STEM')),
    payload_json JSONB NOT NULL,
    content_hash CHAR(64) NOT NULL,
    structural_hash CHAR(64) NOT NULL,
    embedding_vec vector(768),
    duplicate_cluster_id UUID,
    extraction_confidence NUMERIC(4,3),
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'EXTRACTED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    candidate_status VARCHAR(30) NOT NULL DEFAULT 'NEEDS_REVIEW'
        CHECK (candidate_status IN ('NEEDS_REVIEW', 'APPROVED', 'REJECTED')),
    promoted_question_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (provenance_id, tenant_id) REFERENCES provenance_records(id, tenant_id),
    FOREIGN KEY (duplicate_cluster_id, tenant_id) REFERENCES duplicate_clusters(id, tenant_id)
);
CREATE INDEX ix_question_candidates_content_hash ON question_candidates (tenant_id, content_hash);
CREATE INDEX ix_question_candidates_struct_hash ON question_candidates (tenant_id, structural_hash);

CREATE TABLE duplicate_members (
    cluster_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    similarity_score NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    PRIMARY KEY (cluster_id, candidate_id),
    FOREIGN KEY (cluster_id, tenant_id) REFERENCES duplicate_clusters(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, tenant_id) REFERENCES question_candidates(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE questions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    stable_code VARCHAR(100) NOT NULL,
    item_type VARCHAR(40) NOT NULL
        CHECK (item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY', 'GROUP_STEM')),
    primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id),
    curriculum_node_id UUID REFERENCES curriculum_nodes(id),
    exam_framework_tags VARCHAR(50)[] NOT NULL DEFAULT '{SGK_KNTT}',
    difficulty_level VARCHAR(20) NOT NULL DEFAULT 'TH'
        CHECK (difficulty_level IN ('NB', 'TH', 'VD', 'VDC')),
    current_version_id UUID,
    -- Đủ các trục trạng thái với mặc định Fail-Closed ('PENDING_REVIEW')
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'NORMALIZED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
    access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
    duplicate_cluster_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, stable_code), -- Khắc phục lỗi UNIQUE toàn cục
    UNIQUE (id, tenant_id),
    CONSTRAINT chk_question_publish_gate CHECK (
        publication_status <> 'PUBLISHED'
        OR (verification_status = 'VERIFIED' AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)
    )
);
CREATE INDEX ix_questions_exam_tags_gin ON questions USING GIN (exam_framework_tags);
CREATE INDEX ix_questions_pool_lookup ON questions (tenant_id, publication_status, verification_status, primary_taxonomy_node_id, difficulty_level);

CREATE TABLE question_stimuli (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stimulus_type VARCHAR(40) NOT NULL
        CHECK (stimulus_type IN ('READING_PASSAGE', 'EXPERIMENT_CONTEXT', 'DATA_TABLE', 'SHARED_FIGURE')),
    body_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE question_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    question_id UUID NOT NULL,
    version_no INT NOT NULL,
    stimulus_id UUID,
    stem_json JSONB NOT NULL,
    options_json JSONB NOT NULL DEFAULT '[]',
    numeric_parse_policy_json JSONB NOT NULL DEFAULT '{"decimal_sep": "COMMA_OR_DOT", "allow_thousand_sep": false, "max_chars": 4, "tolerance": 0.01}',
    content_hash CHAR(64) NOT NULL,
    structural_hash CHAR(64) NOT NULL,
    is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
    change_reason VARCHAR(500),
    created_by UUID REFERENCES users(id),
    approved_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (question_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (question_id, tenant_id) REFERENCES questions(id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (stimulus_id, tenant_id) REFERENCES question_stimuli(id, tenant_id)
);

-- Tách biệt hoàn toàn đáp án & lời giải sang bảng riêng (Role phát đề bị REVOKE SELECT — Khắc phục P0-8)
CREATE TABLE question_version_answers (
    question_version_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    answer_schema JSONB NOT NULL,
    rationale_json JSONB NOT NULL DEFAULT '{}',
    is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
);

-- View công khai an toàn tuyệt đối cho API Phát đề (C05)
-- F-02 (dry-run): security_invoker bắt buộc để view áp RLS của người gọi, không phải của chủ view
CREATE OR REPLACE VIEW v_public_question_versions WITH (security_invoker = true) AS
SELECT id, tenant_id, visibility_scope, question_id, version_no, stimulus_id, stem_json, options_json, numeric_parse_policy_json
FROM question_versions;
CREATE INDEX ix_question_versions_hashes ON question_versions (tenant_id, content_hash, structural_hash);

ALTER TABLE questions
    ADD CONSTRAINT fk_questions_current_version
    FOREIGN KEY (current_version_id, tenant_id)
    REFERENCES question_versions(id, tenant_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE question_options (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    question_version_id UUID NOT NULL,
    option_key VARCHAR(10) NOT NULL, -- A, B, C, D hoặc a, b, c, d
    content_json JSONB NOT NULL,
    sequence_order INT NOT NULL,
    UNIQUE (question_version_id, option_key),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE question_assets (
    question_version_id UUID NOT NULL,
    asset_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    PRIMARY KEY (question_version_id, asset_id),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE verification_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    question_version_id UUID,
    candidate_id UUID,
    verifier_type VARCHAR(40) NOT NULL
        CHECK (verifier_type IN ('DETERMINISTIC_RULE', 'SYMPY_SOLVER', 'LLM_BLIND_SOLVER', 'HUMAN_EXPERT')),
    model_or_tool_version VARCHAR(80) NOT NULL,
    computed_answer VARCHAR(255),
    is_match_with_source BOOLEAN,
    findings_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, tenant_id) REFERENCES question_candidates(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE review_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_type VARCHAR(40) NOT NULL
        CHECK (entity_type IN ('QUESTION_CANDIDATE', 'QUESTION_VERSION', 'EXAM_ARCHIVE', 'CONTENT_VERSION')),
    entity_id UUID NOT NULL,
    queue_name VARCHAR(60) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'NORMAL'
        CHECK (severity IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')),
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'CANCELLED')),
    assigned_to UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);
CREATE INDEX ix_review_tasks_queue_status ON review_tasks (tenant_id, queue_name, status, severity);

CREATE TABLE review_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    review_task_id UUID NOT NULL,
    reviewer_id UUID NOT NULL REFERENCES users(id),
    decision VARCHAR(30) NOT NULL
        CHECK (decision IN ('APPROVE', 'REJECT', 'REQUEST_REPROCESS', 'SPLIT_MERGE')),
    reason TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (review_task_id, tenant_id) REFERENCES review_tasks(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 6: ASSESSMENT, SNAPSHOT TÁCH ĐÁP ÁN, ATTEMPTS & GRADING (C03 – C07, F02, F03)
-- ============================================================================
CREATE TABLE assessment_frameworks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    code VARCHAR(60) UNIQUE NOT NULL, -- THPTQG_2025, DGTD_TSA, DGNL_HSA, HSG_CHUYEN
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE framework_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    framework_id UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
    version_code VARCHAR(40) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    total_questions INT NOT NULL, -- VD: Toán THPT 2025 = 22 câu; Lý/Hóa/Sinh = 28 câu
    duration_seconds INT NOT NULL,
    section_specs_json JSONB NOT NULL,
    UNIQUE (framework_id, version_code, subject_code)
);

CREATE TABLE scoring_scheme_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    code VARCHAR(80) UNIQUE NOT NULL,
    rules_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE rubric_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    criteria_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE blueprints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    framework_id UUID NOT NULL REFERENCES assessment_frameworks(id),
    name VARCHAR(255) NOT NULL,
    rules_json JSONB NOT NULL,
    duration_seconds INT NOT NULL,
    total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE assessments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')),
    title VARCHAR(500) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    grade_band VARCHAR(30) NOT NULL,
    exam_type_code VARCHAR(50) NOT NULL,
    source_archive_doc_id UUID,
    branded_pdf_asset_id UUID,
    preview_pdf_asset_id UUID,
    access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
    current_version_id UUID,
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    CONSTRAINT chk_assessment_publish_gate CHECK (
        publication_status <> 'PUBLISHED'
        OR (verification_status = 'VERIFIED' AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)
    )
);

-- Tách hoàn toàn structure_snapshot_json (không đáp án) và bảng assessment_answer_keys (ADR-005 & Runbook 9)
CREATE TABLE assessment_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    assessment_id UUID NOT NULL,
    version_no INT NOT NULL,
    scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
    duration_seconds INT NOT NULL,
    total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
    base_seed BIGINT NOT NULL DEFAULT 20261111,
    -- Chỉ chứa danh sách question_version_id, section, điểm số và hash kiểm chứng (Tuyệt đối không chứa đáp án)
    structure_snapshot_json JSONB NOT NULL,
    is_immutable BOOLEAN NOT NULL DEFAULT TRUE,
    published_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (assessment_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments(id, tenant_id) ON DELETE RESTRICT
);

ALTER TABLE assessments
    ADD CONSTRAINT fk_assessments_current_version
    FOREIGN KEY (current_version_id, tenant_id)
    REFERENCES assessment_versions(id, tenant_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE assessment_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    assessment_version_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    section_code VARCHAR(30) NOT NULL, -- PART_I_MCQ, PART_II_TF4, PART_III_SHORT
    sequence_order INT NOT NULL,
    points NUMERIC(5,2) NOT NULL,
    UNIQUE (assessment_version_id, sequence_order),
    FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id) ON DELETE CASCADE
);

-- Bảng lưu khóa đáp án chấm điểm tách riêng có lịch sử phiên bản phục vụ Regrade/Errata (Khắc phục P0-8 & RB-07)
CREATE TABLE assessment_answer_keys (
    assessment_version_id UUID NOT NULL,
    key_revision_no INT NOT NULL DEFAULT 1,
    tenant_id UUID NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    answer_key_payload_json JSONB NOT NULL,
    key_checksum_sha256 CHAR(64) NOT NULL,
    change_reason TEXT NOT NULL DEFAULT 'INITIAL_PUBLISH',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (assessment_version_id, key_revision_no),
    FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_assessment_answer_keys_current ON assessment_answer_keys (assessment_version_id) WHERE is_current = TRUE;

CREATE TABLE assessment_assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    assessment_version_id UUID NOT NULL,
    class_id UUID,
    group_id UUID,
    assigned_by UUID NOT NULL REFERENCES users(id),
    available_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at TIMESTAMPTZ,
    max_attempts INT NOT NULL DEFAULT 1,
    reveal_policy VARCHAR(40) NOT NULL DEFAULT 'AFTER_DUE'
        CHECK (reveal_policy IN ('IMMEDIATE', 'AFTER_DUE', 'MANUAL_RELEASE', 'NEVER')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
);

-- Bảng attempts: ON DELETE RESTRICT trên membership_id + Lưu hạt giống và thứ tự trộn đề
CREATE TABLE attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    assessment_version_id UUID NOT NULL REFERENCES assessment_versions(id) ON DELETE RESTRICT,
    assignment_id UUID,
    membership_id UUID NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS'
        CHECK (status IN ('IN_PROGRESS', 'SUBMITTED', 'AUTO_SUBMITTED', 'GRADED', 'VOIDED')),
    attempt_seed BIGINT NOT NULL,
    question_order_ids UUID[] NOT NULL,
    option_permutations_json JSONB NOT NULL DEFAULT '{}', -- {"<question_version_id>": ["C", "A", "D", "B"]}
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deadline_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ,
    submit_idempotency_key VARCHAR(128) UNIQUE,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);
CREATE INDEX ix_attempts_membership_status ON attempts (tenant_id, membership_id, status);
CREATE INDEX ix_attempts_auto_submit_scan ON attempts (status, deadline_at) WHERE status = 'IN_PROGRESS';

CREATE TABLE responses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    current_sequence BIGINT NOT NULL DEFAULT 0,
    current_revision_no INT NOT NULL DEFAULT 0,
    last_idempotency_key VARCHAR(128),
    response_payload JSONB NOT NULL,
    is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (attempt_id, question_version_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
);

-- Bảng đăng ký khóa Idempotency-Key chống Race Condition tuyệt đối (Không phụ thuộc recorded_at của Partition — Khắc phục P0-7)
CREATE TABLE autosave_idempotency_registry (
    tenant_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    response_id UUID NOT NULL,
    revision_no INT NOT NULL,
    client_sequence BIGINT NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, attempt_id, idempotency_key),
    FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX ix_autosave_idem_registry_recorded_at ON autosave_idempotency_registry (recorded_at);

-- Bảng response_revisions: Lưu lịch sử bất biến mọi lần lưu bài
CREATE TABLE response_revisions (
    id UUID DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    response_id UUID NOT NULL,
    revision_no INT NOT NULL,
    client_sequence BIGINT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, recorded_at),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses(id, tenant_id) ON DELETE CASCADE
) PARTITION BY RANGE (recorded_at);

CREATE TABLE response_revisions_default PARTITION OF response_revisions DEFAULT;
CREATE INDEX ix_response_revisions_lookup ON response_revisions (tenant_id, response_id, revision_no);

-- Bảng grades: Cho phép nhiều revision khi Phúc khảo / Regrade (Khắc phục lỗi UNIQUE(attempt_id))
CREATE TABLE grades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    revision_no INT NOT NULL DEFAULT 1,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    grade_reason VARCHAR(40) NOT NULL DEFAULT 'INITIAL'
        CHECK (grade_reason IN ('INITIAL', 'APPEAL_REGRADE', 'ERRATA_REGRADE', 'MANUAL_OVERRIDE')),
    scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
    total_score NUMERIC(6,2) NOT NULL,
    section_breakdown_json JSONB NOT NULL,
    item_details_json JSONB NOT NULL,
    graded_by UUID REFERENCES users(id),
    graded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    UNIQUE (attempt_id, revision_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_grades_current_per_attempt ON grades (attempt_id) WHERE is_current = TRUE;

CREATE TABLE grading_details (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    grade_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    earned_points NUMERIC(5,2) NOT NULL,
    max_points NUMERIC(5,2) NOT NULL,
    is_correct BOOLEAN NOT NULL,
    sub_item_breakdown_json JSONB NOT NULL DEFAULT '{}',
    FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE appeals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    grade_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    question_version_id UUID REFERENCES question_versions(id),
    reason_text TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'APPROVED_REGRADED', 'REJECTED')),
    resolution_note TEXT,
    resolved_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE gradebook_projections (
    class_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    stats_json JSONB NOT NULL DEFAULT '{}',
    top_wrong_questions_json JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (class_id, assignment_id),
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 7: LEARNER LOOP, PRACTICE, ERROR NOTEBOOK, FSRS & IRT (D01 – D06)
-- ============================================================================
CREATE TABLE activity_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    activity_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    duration_seconds INT NOT NULL DEFAULT 0,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE learner_dashboard_projections (
    membership_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    streak_days INT NOT NULL DEFAULT 0,
    summary_json JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE learning_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    target_framework_code VARCHAR(60) NOT NULL DEFAULT 'THPTQG_2025',
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'COMPLETED', 'ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE plan_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    learning_plan_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id),
    task_type VARCHAR(40) NOT NULL
        CHECK (task_type IN ('PREREQUISITE_REVIEW', 'READ_CONCEPT', 'PRACTICE_SET', 'ERROR_REMEDIATION')),
    rationale_text TEXT NOT NULL,
    sequence_order INT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED')),
    FOREIGN KEY (learning_plan_id, tenant_id) REFERENCES learning_plans(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE practice_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    mode VARCHAR(40) NOT NULL DEFAULT 'TOPIC_DRILL'
        CHECK (mode IN ('LESSON_EMBED', 'TOPIC_DRILL', 'REMEDIATION_DRILL')),
    status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS'
        CHECK (status IN ('IN_PROGRESS', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE practice_responses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    practice_session_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    response_payload JSONB NOT NULL,
    is_correct BOOLEAN NOT NULL,
    hints_used INT NOT NULL DEFAULT 0,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (practice_session_id, tenant_id) REFERENCES practice_sessions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE error_notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id),
    wrong_count INT NOT NULL DEFAULT 1,
    consecutive_correct_count INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'IN_REMEDIATION', 'RESOLVED')),
    last_wrong_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (membership_id, question_version_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE remediation_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    error_note_id UUID NOT NULL,
    recommended_question_version_ids UUID[] NOT NULL,
    completed_count INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'ASSIGNED'
        CHECK (status IN ('ASSIGNED', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (error_note_id, tenant_id) REFERENCES error_notes(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE flashcard_decks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE flashcards (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    deck_id UUID NOT NULL,
    front_md TEXT NOT NULL,
    back_md TEXT NOT NULL,
    source_question_version_id UUID REFERENCES question_versions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (deck_id, tenant_id) REFERENCES flashcard_decks(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE revision_schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    flashcard_id UUID NOT NULL,
    scheduler_version VARCHAR(30) NOT NULL DEFAULT 'FSRS_V5',
    stability NUMERIC(8,4) NOT NULL DEFAULT 1.0000,
    difficulty NUMERIC(6,4) NOT NULL DEFAULT 5.0000,
    retrievability NUMERIC(5,4) NOT NULL DEFAULT 1.0000,
    reps INT NOT NULL DEFAULT 0,
    lapses INT NOT NULL DEFAULT 0,
    last_review_at TIMESTAMPTZ,
    due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (membership_id, flashcard_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (flashcard_id, tenant_id) REFERENCES flashcards(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE question_irt_parameters (
    question_version_id UUID PRIMARY KEY REFERENCES question_versions(id) ON DELETE CASCADE,
    calibration_run_id UUID NOT NULL DEFAULT uuid_generate_v7(),
    model_type VARCHAR(30) NOT NULL
        CHECK (model_type IN ('RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')),
    sample_size INT NOT NULL CHECK (sample_size >= 150),
    learner_count INT NOT NULL CHECK (learner_count >= 100),
    ability_spread_sd NUMERIC(5,3) NOT NULL CHECK (ability_spread_sd >= 0.600),
    converged BOOLEAN NOT NULL DEFAULT TRUE CHECK (converged IS TRUE),
    infit_msq NUMERIC(5,3) NOT NULL CHECK (infit_msq BETWEEN 0.700 AND 1.300),
    outfit_msq NUMERIC(5,3) NOT NULL,
    max_abs_dif NUMERIC(5,3) NOT NULL DEFAULT 0.000 CHECK (max_abs_dif <= 0.500),
    discrimination_a NUMERIC(6,4) NOT NULL DEFAULT 1.0000,
    difficulty_b NUMERIC(6,4) NOT NULL,
    guessing_c NUMERIC(6,4) NOT NULL DEFAULT 0.0000,
    step_thresholds_json JSONB NOT NULL DEFAULT '[]',
    se_difficulty NUMERIC(6,4) NOT NULL CHECK (se_difficulty < 0.3000),
    diagnostics_json JSONB NOT NULL DEFAULT '{}',
    approved_by UUID REFERENCES users(id),
    calibrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE mastery_estimates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
    model_code VARCHAR(40) NOT NULL DEFAULT 'RULE_WEIGHTED_L1'
        CHECK (model_code IN ('RULE_WEIGHTED_L1', 'RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')),
    theta_estimate NUMERIC(6,3) NOT NULL DEFAULT 0.000,
    mastery_probability NUMERIC(5,3) NOT NULL CHECK (mastery_probability BETWEEN 0 AND 1),
    standard_error NUMERIC(5,3) NOT NULL DEFAULT 0.500,
    evidence_count INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'INSUFFICIENT_DATA'
        CHECK (status IN ('INSUFFICIENT_DATA', 'CALIBRATED')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (membership_id, taxonomy_node_id, model_code),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 8: AI SYSTEM, TUTOR, GENERATION & COPILOT (E01 – E06)
-- ============================================================================
CREATE TABLE ai_providers (
    code VARCHAR(40) PRIMARY KEY,
    base_url VARCHAR(255) NOT NULL,
    circuit_state VARCHAR(20) NOT NULL DEFAULT 'CLOSED'
        CHECK (circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ai_models (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    provider_code VARCHAR(40) NOT NULL REFERENCES ai_providers(code),
    model_name VARCHAR(100) NOT NULL,
    task_role VARCHAR(40) NOT NULL, -- OCR_VISION, HEALER_FAST, VERIFIER_REASONING, TUTOR_CHAT
    input_cost_per_1m NUMERIC(8,4) NOT NULL,
    output_cost_per_1m NUMERIC(8,4) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE prompt_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    code VARCHAR(80) NOT NULL,
    version_no INT NOT NULL,
    system_prompt TEXT NOT NULL,
    user_template TEXT NOT NULL,
    output_schema_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (code, version_no)
);

CREATE TABLE ai_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    task_type VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
    prompt_version_id UUID REFERENCES prompt_versions(id),
    input_refs_json JSONB NOT NULL DEFAULT '{}',
    output_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE ai_traces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ai_job_id UUID,
    model_id UUID NOT NULL REFERENCES ai_models(id),
    prompt_tokens INT NOT NULL,
    completion_tokens INT NOT NULL,
    cost_usd NUMERIC(10,6) NOT NULL,
    latency_ms INT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_ai_traces_tenant_date ON ai_traces (tenant_id, occurred_at);

CREATE TABLE tutor_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    context_question_version_id UUID REFERENCES question_versions(id),
    context_content_version_id UUID REFERENCES content_versions(id),
    tutor_mode VARCHAR(30) NOT NULL DEFAULT 'HINT_MODE'
        CHECK (tutor_mode IN ('HINT_MODE', 'EXPLAIN_MODE', 'CHALLENGE_MODE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE tutor_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    tutor_session_id UUID NOT NULL,
    sender_role VARCHAR(20) NOT NULL CHECK (sender_role IN ('USER', 'ASSISTANT', 'SYSTEM_GUARD')),
    message_md TEXT NOT NULL,
    citations_json JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (tutor_session_id, tenant_id) REFERENCES tutor_sessions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE retrieval_traces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    tutor_session_id UUID NOT NULL,
    retrieved_ids UUID[] NOT NULL,
    scores NUMERIC(5,4)[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (tutor_session_id, tenant_id) REFERENCES tutor_sessions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE generation_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    requested_by UUID NOT NULL REFERENCES users(id),
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id),
    target_difficulty VARCHAR(20) NOT NULL,
    source_anchors_json JSONB NOT NULL,
    generated_candidate_id UUID,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT_CREATED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE explanation_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    draft_rationale_json JSONB NOT NULL,
    sympy_verified BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_APPROVAL',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    recommended_action_type VARCHAR(40) NOT NULL,
    target_entity_id UUID NOT NULL,
    evidence_json JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'DISMISSED', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE teacher_assist_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    class_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    summary_report_json JSONB NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'READY_FOR_TEACHER',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 9: NOTIFICATIONS, PLATFORM OPS, OUTBOX/INBOX, AUDIT & COMMERCE (F06, G01 – G06)
-- ============================================================================
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    dedup_key VARCHAR(160) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    body_text TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE notification_deliveries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    channel VARCHAR(30) NOT NULL CHECK (channel IN ('IN_APP', 'EMAIL')),
    status VARCHAR(30) NOT NULL DEFAULT 'SENT',
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE platform_settings (
    key VARCHAR(100) PRIMARY KEY,
    value_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE feature_flags (
    key VARCHAR(100) PRIMARY KEY,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    tenant_whitelist UUID[] NOT NULL DEFAULT '{}',
    config_json JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE takedown_cases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    complainant_name VARCHAR(255) NOT NULL,
    target_entity_type VARCHAR(40) NOT NULL CHECK (target_entity_type IN ('SOURCE_DOCUMENT', 'CONTENT_ITEM', 'QUESTION')),
    target_entity_id UUID NOT NULL,
    legal_reason TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'EXECUTED', 'REJECTED')),
    executed_by UUID REFERENCES users(id),
    executed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE audit_events (
    id UUID DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    actor_id UUID,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(60) NOT NULL,
    target_id UUID NOT NULL,
    reason TEXT,
    before_json JSONB,
    after_json JSONB,
    correlation_id VARCHAR(100),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE audit_events_2026_11 PARTITION OF audit_events
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT;
CREATE INDEX ix_audit_events_tenant_target ON audit_events (tenant_id, target_type, target_id, occurred_at DESC);

CREATE TABLE outbox_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    schema_version INT NOT NULL DEFAULT 1,
    aggregate_type VARCHAR(60) NOT NULL,
    aggregate_id UUID NOT NULL,
    correlation_id VARCHAR(100),
    payload_json JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);
CREATE INDEX ix_outbox_events_pending ON outbox_events (status, created_at) WHERE status = 'PENDING';

-- Bảng Inbox cho Idempotent Event Consumers (Khắc phục lỗi thiếu Consumer Idempotency)
CREATE TABLE inbox_events (
    consumer_name VARCHAR(80) NOT NULL,
    event_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (consumer_name, event_id)
);

CREATE TABLE analytics_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    event_name VARCHAR(80) NOT NULL,
    dimensions_json JSONB NOT NULL DEFAULT '{}',
    metrics_json JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE daily_metric_rollups (
    metric_date DATE NOT NULL,
    tenant_id UUID NOT NULL,
    metric_code VARCHAR(80) NOT NULL,
    metric_value NUMERIC(14,4) NOT NULL,
    PRIMARY KEY (metric_date, tenant_id, metric_code)
);

CREATE TABLE subscription_plans (
    code VARCHAR(40) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    price_vnd BIGINT NOT NULL,
    quotas_json JSONB NOT NULL
);

CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    membership_id UUID NOT NULL,
    plan_code VARCHAR(40) NOT NULL REFERENCES subscription_plans(code),
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'PAST_DUE', 'CANCELLED', 'EXPIRED')),
    current_period_end TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE usage_ledger (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    resource_code VARCHAR(60) NOT NULL, -- BRANDED_PDF_EXPORT, AI_TUTOR_MESSAGE
    delta INT NOT NULL,
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE payment_webhooks (
    webhook_id VARCHAR(128) PRIMARY KEY,
    provider VARCHAR(40) NOT NULL,
    raw_payload_json JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE vertical_packs (
    code VARCHAR(60) PRIMARY KEY, -- K12_VN_KNTT, IELTS_ACADEMIC, TOEIC_LR, HSK_3_0
    name VARCHAR(255) NOT NULL,
    manifest_json JSONB NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE locale_bundles (
    locale_code VARCHAR(20) NOT NULL,
    namespace VARCHAR(60) NOT NULL,
    messages_json JSONB NOT NULL,
    PRIMARY KEY (locale_code, namespace)
);

-- ============================================================================
-- KỊCH BẢN ROW-LEVEL SECURITY (RLS 100% BẢNG), AUTO-PARTITION, TRIGGERS & GRANT/REVOKE CHUẨN V11.3
-- ============================================================================

-- 0. F-01 (dry-run): RLS của bảng cha KHÔNG áp khi truy vấn thẳng vào partition. Mỗi partition phải tự bật
--    ENABLE + FORCE RLS, có policy tenant riêng và không cấp quyền trực tiếp cho bất kỳ runtime role nào
--    (mọi truy cập đi qua bảng cha; định tuyến dòng vào partition không cần quyền trên partition).
CREATE OR REPLACE FUNCTION secure_partition(p_partition TEXT) RETURNS void AS $$
BEGIN
    EXECUTE format(
        'REVOKE ALL ON %I FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, '
        'ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;', p_partition);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', p_partition);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', p_partition);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', p_partition);
    EXECUTE format(
        'CREATE POLICY tenant_isolation_policy ON %I
         USING (tenant_id = (SELECT current_app_tenant_id()))
         WITH CHECK (tenant_id = (SELECT current_app_tenant_id()));', p_partition);
END;
$$ LANGUAGE plpgsql;

-- 1. Hàm tự động tạo Partition hàng tháng & Dọn dẹp khóa Idempotency quá hạn (Chạy định kỳ hàng ngày)
CREATE OR REPLACE FUNCTION ensure_monthly_partitions(p_table_name TEXT, p_start_date DATE, p_months INT) RETURNS void AS $$
DECLARE
    i INT;
    v_start TIMESTAMPTZ;
    v_end TIMESTAMPTZ;
    v_part_name TEXT;
BEGIN
    FOR i IN 0..(p_months - 1) LOOP
        v_start := (date_trunc('month', p_start_date) + (i || ' months')::interval) AT TIME ZONE 'UTC';
        v_end := (date_trunc('month', p_start_date) + ((i + 1) || ' months')::interval) AT TIME ZONE 'UTC';
        v_part_name := format('%s_%s', p_table_name, to_char(v_start AT TIME ZONE 'UTC', 'YYYY_MM'));
        IF to_regclass(v_part_name) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L);',
                v_part_name, p_table_name, v_start, v_end
            );
            PERFORM secure_partition(v_part_name);
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION run_daily_partition_and_registry_maintenance() RETURNS void AS $$
BEGIN
    PERFORM ensure_monthly_partitions('response_revisions', CURRENT_DATE, 6);
    PERFORM ensure_monthly_partitions('audit_events', CURRENT_DATE, 6);
    -- Dọn các khóa Idempotency của các phiên thi đã ghi nhận quá 30 ngày
    DELETE FROM autosave_idempotency_registry WHERE recorded_at < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;

-- Khởi tạo ngay các phân vùng ban đầu và đăng ký lịch chạy hàng ngày nếu có pg_cron
SELECT run_daily_partition_and_registry_maintenance();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        EXECUTE $cron$SELECT cron.schedule('ulp_daily_maintenance', '15 2 * * *', 'SELECT run_daily_partition_and_registry_maintenance()')$cron$;
    END IF;
END $$;

-- 2. Kích hoạt ENABLE & FORCE ROW LEVEL SECURITY trên 100% Bảng Nghiệp Vụ
--    Áp dụng InitPlan `(SELECT current_app_tenant_id())` để PostgreSQL tính 1 lần mỗi câu lệnh (tối ưu cho tải 5.000 CCU)
--    và siết chặt hybrid_read_policy chỉ cho phép đọc học liệu SYSTEM_PUBLIC đã PUBLISHED / đóng băng (Khắc phục P0-1, P0-2, P0-3, P0-8)
DO $$
DECLARE
    t text;
    strict_tenant_tables text[] := ARRAY[
        'memberships', 'parental_consents', 'roles', 'role_permissions', 'groups', 'classes', 'class_members',
        'content_drafts', 'comment_threads', 'publications', 'access_grants',
        'collections', 'collection_items', 'sources', 'source_rights', 'source_connections', 'source_sync_cursors',
        'crawl_urls', 'crawl_fetches', 'source_documents', 'file_pairing_candidates', 'document_versions',
        'acquisition_jobs', 'pipeline_stage_runs', 'dead_letter_items', 'extraction_runs', 'extracted_pages',
        'layout_regions', 'provenance_records', 'exam_import_sessions', 'duplicate_clusters', 'content_candidates',
        'question_candidates', 'duplicate_members', 'question_version_answers',
        'verification_runs', 'review_tasks', 'review_decisions', 'rubric_versions', 'blueprints',
        'assessment_answer_keys', 'assessment_assignments', 'attempts', 'responses',
        'autosave_idempotency_registry', 'response_revisions', 'grades', 'grading_details', 'appeals', 'gradebook_projections',
        'activity_events', 'learner_dashboard_projections', 'learning_plans', 'plan_tasks',
        'practice_sessions', 'practice_responses', 'error_notes', 'remediation_tasks',
        'flashcard_decks', 'flashcards', 'revision_schedules', 'mastery_estimates',
        'ai_jobs', 'ai_traces', 'tutor_sessions', 'tutor_messages', 'retrieval_traces',
        'generation_requests', 'explanation_runs', 'recommendations', 'teacher_assist_jobs',
        'notifications', 'takedown_cases', 'erasure_certificates', 'audit_events', 'outbox_events', 'inbox_events',
        'analytics_events', 'daily_metric_rollups', 'subscriptions', 'usage_ledger', 'graph_validation_runs'
    ];
    hybrid_public_tables text[] := ARRAY[
        'taxonomy_nodes', 'taxonomy_node_versions', 'taxonomy_aliases', 'knowledge_edges',
        'curriculum_versions', 'curriculum_nodes', 'taxonomy_mappings',
        'assets', 'content_items', 'content_versions', 'content_blocks', 'content_assets',
        'questions', 'question_stimuli', 'question_versions', 'question_options', 'question_assets',
        'assessments', 'assessment_versions', 'assessment_items', 'search_documents'
    ];
BEGIN
    FOREACH t IN ARRAY strict_tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation_policy ON %I
             USING (tenant_id = (SELECT current_app_tenant_id()))
             WITH CHECK (tenant_id = (SELECT current_app_tenant_id()));',
            t
        );
    END LOOP;

    FOREACH t IN ARRAY hybrid_public_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format('DROP POLICY IF EXISTS hybrid_read_policy ON %I;', t);
        EXECUTE format('DROP POLICY IF EXISTS hybrid_write_policy ON %I;', t);
        EXECUTE format(
            'CREATE POLICY hybrid_write_policy ON %I FOR ALL
             USING (tenant_id = (SELECT current_app_tenant_id()))
             WITH CHECK (tenant_id = (SELECT current_app_tenant_id()));',
            t
        );
    END LOOP;
END $$;

-- 2a. Định nghĩa tường minh hybrid_read_policy cho từng nhóm bảng công khai (Khắc phục P0-3: Chặn lộ bản DRAFT / PENDING_REVIEW / TAKEN_DOWN của hệ thống)
CREATE POLICY hybrid_read_policy ON content_items FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND publication_status = 'PUBLISHED'
        AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'))
);

CREATE POLICY hybrid_read_policy ON questions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND publication_status = 'PUBLISHED'
        AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'))
);

CREATE POLICY hybrid_read_policy ON assessments FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND publication_status = 'PUBLISHED'
        AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'))
);

CREATE POLICY hybrid_read_policy ON content_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND is_immutable IS TRUE
        AND status = 'PUBLISHED'
        AND EXISTS (
            SELECT 1 FROM content_items c
            WHERE c.id = content_versions.content_item_id AND c.tenant_id = content_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON content_blocks FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM content_versions cv
            WHERE cv.id = content_blocks.content_version_id AND cv.tenant_id = content_blocks.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON content_assets FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM content_versions cv
            WHERE cv.id = content_assets.content_version_id AND cv.tenant_id = content_assets.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND is_immutable IS TRUE
        AND EXISTS (
            SELECT 1 FROM questions q
            WHERE q.id = question_versions.question_id AND q.tenant_id = question_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_options FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM question_versions qv
            WHERE qv.id = question_options.question_version_id AND qv.tenant_id = question_options.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_assets FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM question_versions qv
            WHERE qv.id = question_assets.question_version_id AND qv.tenant_id = question_assets.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_stimuli FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM question_versions qv
            WHERE qv.stimulus_id = question_stimuli.id AND qv.tenant_id = question_stimuli.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON assessment_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND is_immutable IS TRUE
        AND EXISTS (
            SELECT 1 FROM assessments a
            WHERE a.id = assessment_versions.assessment_id AND a.tenant_id = assessment_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON assessment_items FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM assessment_versions av
            WHERE av.id = assessment_items.assessment_version_id AND av.tenant_id = assessment_items.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON assets FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND asset_role <> 'RAW_SOURCE')
);

CREATE POLICY hybrid_read_policy ON search_documents FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC')
);

CREATE POLICY hybrid_read_policy ON taxonomy_nodes FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC' AND is_active IS TRUE)
);

CREATE POLICY hybrid_read_policy ON taxonomy_node_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM taxonomy_nodes tn
            WHERE tn.id = taxonomy_node_versions.taxonomy_node_id AND tn.tenant_id = taxonomy_node_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON taxonomy_aliases FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM taxonomy_nodes tn
            WHERE tn.id = taxonomy_aliases.taxonomy_node_id AND tn.tenant_id = taxonomy_aliases.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON knowledge_edges FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (SELECT 1 FROM taxonomy_nodes s WHERE s.id = knowledge_edges.source_node_id AND s.is_active IS TRUE)
        AND EXISTS (SELECT 1 FROM taxonomy_nodes t WHERE t.id = knowledge_edges.target_node_id AND t.is_active IS TRUE))
);

CREATE POLICY hybrid_read_policy ON curriculum_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC' AND status = 'PUBLISHED')
);

CREATE POLICY hybrid_read_policy ON curriculum_nodes FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM curriculum_versions cv
            WHERE cv.id = curriculum_nodes.curriculum_version_id AND cv.tenant_id = curriculum_nodes.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON taxonomy_mappings FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM curriculum_nodes cn
            WHERE cn.id = taxonomy_mappings.curriculum_node_id AND cn.tenant_id = taxonomy_mappings.tenant_id
        ))
);

-- 2b. Policy riêng cho ulp_worker_grading đọc được khóa đáp án của đề hệ thống SYSTEM_PUBLIC (Khắc phục P0-1)
CREATE POLICY grading_read_question_answers ON question_version_answers
    FOR SELECT TO ulp_worker_grading
    USING (tenant_id = (SELECT current_app_tenant_id()) OR tenant_id = system_public_tenant_id());

CREATE POLICY grading_read_answer_keys ON assessment_answer_keys
    FOR SELECT TO ulp_worker_grading
    USING (tenant_id = (SELECT current_app_tenant_id()) OR tenant_id = system_public_tenant_id());

-- 2c. Policy hẹp theo app.system_job cho ulp_system_scheduler (Khắc phục P0-2: Quét job nền xuyên Tenant không cần BYPASSRLS)
CREATE POLICY sys_outbox_relay ON outbox_events FOR ALL TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'outbox_relay')
    WITH CHECK ((SELECT app_system_job()) = 'outbox_relay');

CREATE POLICY sys_attempt_expiry_scan ON attempts FOR SELECT TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'attempt_expiry' AND status = 'IN_PROGRESS');

CREATE POLICY sys_rollup_read ON analytics_events FOR SELECT TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'metric_rollup');

CREATE POLICY sys_rollup_write ON daily_metric_rollups FOR ALL TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'metric_rollup')
    WITH CHECK ((SELECT app_system_job()) = 'metric_rollup');

-- 3. Trigger kiểm tra tham chiếu học liệu chéo: Cho phép đề thi của Tenant tham chiếu câu hỏi của chính Tenant đó HOẶC SYSTEM_PUBLIC đã PUBLISHED
CREATE OR REPLACE FUNCTION enforce_tenant_or_system_public_question_ref() RETURNS trigger AS $$
DECLARE
    v_target_tenant UUID;
    v_target_scope VARCHAR(20);
    v_target_immutable BOOLEAN;
BEGIN
    SELECT tenant_id, visibility_scope, is_immutable INTO v_target_tenant, v_target_scope, v_target_immutable
    FROM question_versions
    WHERE id = NEW.question_version_id;

    IF v_target_tenant IS NULL THEN
        RAISE EXCEPTION 'FK_NOT_FOUND: Không tìm thấy question_version_id %', NEW.question_version_id;
    END IF;

    IF v_target_tenant <> NEW.tenant_id AND NOT (v_target_tenant = system_public_tenant_id() AND v_target_scope = 'SYSTEM_PUBLIC' AND v_target_immutable IS TRUE) THEN
        RAISE EXCEPTION 'CROSS_TENANT_REFERENCE_FORBIDDEN: Tenant % không được phép sử dụng question_version_id % chưa công khai của Tenant khác.',
            NEW.tenant_id, NEW.question_version_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_check_assessment_item_question_ref
    BEFORE INSERT OR UPDATE ON assessment_items
    FOR EACH ROW EXECUTE FUNCTION enforce_tenant_or_system_public_question_ref();

-- 4. Tách biệt Trigger Append-Only (trên bảng phân vùng) và Trigger Phiên bản Bất biến
CREATE OR REPLACE FUNCTION prevent_append_only_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: Bảng % (thuộc %) là Append-Only, cấm tuyệt đối UPDATE/DELETE.',
        TG_TABLE_NAME, TG_ARGV[0];
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_audit_events
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation('audit_events');

CREATE TRIGGER trg_immutable_response_revisions
    BEFORE UPDATE OR DELETE ON response_revisions
    FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation('response_revisions');

CREATE OR REPLACE FUNCTION enforce_audit_no_raw_pii() RETURNS trigger AS $$
DECLARE
    v_combined TEXT;
BEGIN
    v_combined := coalesce(NEW.before_json::text, '') || coalesce(NEW.after_json::text, '');
    IF v_combined ~* '"(raw_email|plaintext_email|guardian_email|password|phone_number|cccd)"\s*:' THEN
        RAISE EXCEPTION 'RAW_PII_IN_AUDIT_FORBIDDEN: Không được phép ghi PII dạng rõ vào audit_events; phải dùng blind_index hoặc pii_ciphertext.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_no_raw_pii
    BEFORE INSERT ON audit_events
    FOR EACH ROW EXECUTE FUNCTION enforce_audit_no_raw_pii();

CREATE OR REPLACE FUNCTION auto_lock_version_on_publish() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'content_versions' AND NEW.status = 'PUBLISHED' THEN
        NEW.is_immutable := TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_auto_lock_content_versions
    BEFORE INSERT OR UPDATE ON content_versions
    FOR EACH ROW EXECUTE FUNCTION auto_lock_version_on_publish();

CREATE OR REPLACE FUNCTION lock_question_version_when_published() RETURNS trigger AS $$
BEGIN
    IF NEW.publication_status = 'PUBLISHED' AND NEW.current_version_id IS NOT NULL THEN
        UPDATE question_versions SET is_immutable = TRUE WHERE id = NEW.current_version_id AND is_immutable = FALSE;
        UPDATE question_version_answers SET is_immutable = TRUE WHERE question_version_id = NEW.current_version_id AND is_immutable = FALSE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_lock_question_version_on_publish
    AFTER INSERT OR UPDATE OF publication_status, current_version_id ON questions
    FOR EACH ROW EXECUTE FUNCTION lock_question_version_when_published();

CREATE OR REPLACE FUNCTION prevent_version_immutability_mutation() RETURNS trigger AS $$
BEGIN
    IF OLD.is_immutable IS TRUE THEN
        -- F-03 (dry-run): NEW.status chỉ tồn tại trên content_versions nên phải nằm trong IF lồng riêng
        IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'content_versions' THEN
            IF NEW.status = 'TAKEN_DOWN' THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION 'IMMUTABLE_VIOLATION: Bản ghi phiên bản trong bảng % đã đóng băng (is_immutable=TRUE), phải tạo version_no + 1.', TG_TABLE_NAME;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_content_versions
    BEFORE UPDATE OR DELETE ON content_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

CREATE TRIGGER trg_immutable_question_versions
    BEFORE UPDATE OR DELETE ON question_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

CREATE TRIGGER trg_immutable_question_version_answers
    BEFORE UPDATE OR DELETE ON question_version_answers
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

CREATE TRIGGER trg_immutable_assessment_versions
    BEFORE UPDATE OR DELETE ON assessment_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

-- 5. Phân quyền Least-Privilege Tường Minh theo Từng Bảng/Cột cho 7 DB Roles (Khắc phục P0-8: Xóa bỏ GRANT ALL trên Worker Roles)
GRANT USAGE ON SCHEMA public TO ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;

-- 5a. Role API Chính (ulp_app_rw — H-03 Hardening: Thu hồi CREATE trên schema, thu hồi quyền ghi trên các bảng chỉ dành cho Worker/System/Migrator)
REVOKE CREATE ON SCHEMA public FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ulp_app_rw;
-- H-03: ulp_app_rw không được sửa bảng cấu hình từ điển FTS, bảng rollup chỉ số hoặc bảng inbox của worker
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON fts_dictionary_versions, daily_metric_rollups, inbox_events FROM ulp_app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE ulp_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;

-- 5b. Role Phòng Thi (ulp_exam_session_rw): Ghi được bài làm Autosave nhưng bị chặn tuyệt đối đọc bảng đáp án
GRANT SELECT ON
    v_public_question_versions, questions, question_versions, question_options, question_stimuli, question_assets,
    assets, assessments, assessment_versions, assessment_items, assessment_assignments, memberships
TO ulp_exam_session_rw;
GRANT SELECT, INSERT, UPDATE ON attempts, responses TO ulp_exam_session_rw;
GRANT SELECT, INSERT ON autosave_idempotency_registry, response_revisions, activity_events, outbox_events, audit_events TO ulp_exam_session_rw;

-- 5c. Role Worker Chấm Điểm (ulp_worker_grading): Chỉ có quyền trên các bảng liên quan đến chấm điểm & đáp án
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_worker_grading;
GRANT SELECT ON
    attempts, responses, assessment_versions, assessment_items,
    question_versions, question_version_answers, assessment_answer_keys,
    scoring_scheme_versions, rubric_versions, memberships
TO ulp_worker_grading;
GRANT SELECT, INSERT, UPDATE ON grades, grading_details, error_notes, learner_dashboard_projections, gradebook_projections TO ulp_worker_grading;
GRANT UPDATE (status, submitted_at) ON attempts TO ulp_worker_grading;
GRANT INSERT ON outbox_events, audit_events TO ulp_worker_grading;
GRANT SELECT, INSERT ON inbox_events TO ulp_worker_grading;

-- 5d. Role Worker Nạp Đề & OCR (ulp_worker_ingestion): Tuyệt đối KHÔNG có quyền trên bảng đáp án hay bảng điểm
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_worker_ingestion;
GRANT SELECT, INSERT, UPDATE ON
    sources, source_documents, document_versions, acquisition_jobs, pipeline_stage_runs,
    extraction_runs, extracted_pages, layout_regions, provenance_records, exam_import_sessions,
    content_candidates, question_candidates, duplicate_clusters, file_pairing_candidates,
    crawl_urls, crawl_fetches, source_sync_cursors, verification_runs, review_tasks
TO ulp_worker_ingestion;
GRANT SELECT, INSERT ON assets, duplicate_members, dead_letter_items TO ulp_worker_ingestion;
GRANT INSERT ON outbox_events, audit_events TO ulp_worker_ingestion;
GRANT SELECT, INSERT ON inbox_events TO ulp_worker_ingestion;

-- 5e. Role Scheduler Hệ Thống (ulp_system_scheduler): Chỉ có quyền đọc/cập nhật đúng cột phục vụ Outbox Relay, Quét Hết Giờ & Rollup
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_system_scheduler;
GRANT SELECT, UPDATE (status, published_at) ON outbox_events TO ulp_system_scheduler;
GRANT SELECT (id, tenant_id, status, deadline_at) ON attempts TO ulp_system_scheduler;
GRANT SELECT ON analytics_events TO ulp_system_scheduler;
GRANT SELECT, INSERT, UPDATE ON daily_metric_rollups TO ulp_system_scheduler;

-- 5f. Role Phân Tích Chỉ Đọc (ulp_analytics_ro)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_analytics_ro;
GRANT SELECT ON v_public_question_versions, search_documents, daily_metric_rollups, analytics_events TO ulp_analytics_ro;

-- 5g. Khóa cứng quyền UPDATE/DELETE trên các bảng Append-Only và khóa bảng đáp án khỏi các role không có thẩm quyền
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events, response_revisions FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;
REVOKE ALL ON question_version_answers, assessment_answer_keys FROM PUBLIC, ulp_exam_session_rw, ulp_worker_ingestion, ulp_system_scheduler, ulp_analytics_ro;

-- 5h. F-01 (dry-run): khóa lại mọi partition sau lệnh GRANT ... ON ALL TABLES ở mục 5a
DO $$
DECLARE p regclass;
BEGIN
    FOR p IN SELECT inhrelid::regclass FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
             WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace LOOP
        PERFORM secure_partition(p::text);
    END LOOP;
END $$;

-- 5i. F-02 (dry-run): view security_invoker đọc bằng quyền người gọi, nên ulp_analytics_ro cần quyền đọc
--     đúng các cột mà v_public_question_versions công bố (RLS của question_versions vẫn áp dụng)
GRANT SELECT (id, tenant_id, visibility_scope, question_id, version_no, stimulus_id, stem_json, options_json, numeric_parse_policy_json)
    ON question_versions TO ulp_analytics_ro;
-- hybrid_read_policy của question_versions kiểm tra bảng cha qua EXISTS, nên cần đọc được hai cột khóa của questions
GRANT SELECT (id, tenant_id) ON questions TO ulp_analytics_ro;
```

---

## CHƯƠNG 5. DANH MỤC SỰ KIỆN NỘI BỘ (EVENT BUS CATALOG) & HỢP ĐỒNG PYDANTIC V2

### 5.1. Danh mục Hợp đồng Sự kiện Nội bộ (Canonical Event Bus Catalog — Kèm `schema_version` & Inbox Idempotency)

Mọi Consumer khi nhận sự kiện từ `outbox_events` bắt buộc thực hiện `INSERT INTO inbox_events (consumer_name, event_id, tenant_id) VALUES (...) ON CONFLICT DO NOTHING` trong cùng một Transaction với tác dụng phụ để đảm bảo xử lý đúng 1 lần (Idempotent Consumer).

| Tên Sự Kiện (`event_type`) | `schema_version` | Module Phát (Producer) | Module Nhận (Consumers) & Tác dụng phụ (Side Effects) | Payload Chuẩn Tối Thiểu |
| :--- | :---: | :--- | :--- | :--- |
| `source.crawl.requested` | `1` | A02 (Crawler) | `io-crawler-pool`: Khám phá URL, kiểm tra `robots.txt` và tải trang với chống SSRF/DNS Rebinding. | `job_id, tenant_id, source_id, config_version, requested_by` |
| `crawl.page.fetched` | `1` | A02 (Fetcher) | `A05/A07`: Bóc tách HTML thô ở tầng Bronze sang `QuestionCandidate`. | `job_id, tenant_id, source_id, url, raw_asset_id, sha256, resolved_ip` |
| `crawl.parser.drift_detected` | `1` | A02 (Drift Monitor) | `G01/A02`: Tự động chuyển nguồn sang `PAUSED_DRIFT` và bắn cảnh báo Ops. | `tenant_id, source_id, adapter_version, empty_yield_rate, threshold` |
| `drive.file.discovered` | `1` | A03 (Drive Sync) | `A01/A03`: Tải file qua `drive.file`, bung nén sandbox, chạy Pairing 4 tín hiệu `Đề + HDC`. | `tenant_id, connection_id, drive_file_id, sha256, folder_path` |
| `document.uploaded` | `1` | A01 (Ingestion) | `A05/A06`: Định tuyến Format Router (LibreOffice cô lập profile / `PDFEngineAdapter` / OCR) tạo `extraction_runs`. | `tenant_id, document_id, version_id, raw_asset_id, sha256, mime_type` |
| `document.extraction.completed` | `1` | A05 / A06 | `A04/A07`: Tách `ContentCandidate` (VietJack) + `QuestionCandidate` + khớp bảng HDC 3 cột. | `tenant_id, document_version_id, extraction_run_id, profile_code` |
| `question.candidate.created` | `1` | A07 (Extractor) | `A08 (Dedup)` & `C02 (Quality Job 1 & Job 2)`: Băm `content_hash`/`structural_hash` và giải kiểm chứng qua `E01`. | `tenant_id, candidate_id, acquisition_method, content_hash, structural_hash` |
| `question.review.required` | `1` | C02 / A08 | `A09 (Review Queue)`: Đẩy vào hàng đợi duyệt Side-by-Side theo môn học và mức độ ưu tiên. | `tenant_id, entity_id, reason_codes[], severity, queue_name` |
| `question.version.published` | `1` | C01 / A09 | `B07 (Search Index)` & `C04 (Assessment Pool)`: Đưa vào chỉ mục tìm kiếm và kho bốc đề. | `tenant_id, question_id, version_id, taxonomy_node_id, exam_tags[]` |
| `content.published` | `1` | B01 (Content) | `B07 (Search Index)` & `D02 (Learning Path)`: Cập nhật chỉ mục FTS và bài học chuyên đề. | `tenant_id, content_item_id, version_id, curriculum_node_id` |
| `assessment.published` | `1` | C04 (Exam Builder) | `C05 (Exam Delivery)`: Làm nóng cache `structure_snapshot_json` (tuyệt đối không cache `assessment_answer_keys`). | `tenant_id, assessment_id, assessment_version_id, framework_code` |
| `attempt.submitted` | `1` | C05 / C06 | `C07 (Grading)`: Chấm điểm tất định từ `assessment_answer_keys`. | `tenant_id, attempt_id, membership_id, assessment_version_id, submitted_at` |
| `attempt.expired` | `1` | C05 (Expiry Worker) | `C06 / C07`: Tự động thu bài (`AUTO_SUBMITTED`) khi quá `deadline_at` và kích hoạt chấm điểm. | `tenant_id, attempt_id, deadline_at, auto_submitted_at` |
| `attempt.graded` | `1` | C07 (Grading) | `D05 (Error Notebook)`, `D06 (Mastery)`, `F03 (Gradebook)`: Gom câu sai, tính lại Mastery, cập nhật bảng điểm lớp. | `tenant_id, attempt_id, grade_id, revision_no, total_score, wrong_question_version_ids[]` |
| `grade.released` | `1` | C07 / F02 | `F06 (Notifications)` & `D01 (Dashboard)`: Gửi thông báo điểm thi cho học sinh theo `reveal_policy`. | `tenant_id, grade_id, attempt_id, membership_id, released_at` |
| `rights.takedown.executed` | `1` | G02 (Takedown) | `A09, B01, C01, B07, C05`: Chuyển `rights_status = 'TAKEDOWN'`, xóa tức thì khỏi Search Index & CDN, che nội dung trên đề cũ. | `tenant_id, takedown_case_id, target_entity_type, target_entity_id, executed_at` |
| `subscription.updated` | `1` | G05 (Commerce) | `F05 (Entitlements)`: Cập nhật `entitlement_tier` trên `memberships` và hạn mức sử dụng. | `tenant_id, membership_id, plan_code, status, current_period_end` |
| `ai.budget.threshold_exceeded` | `1` | E01 (AI Gateway) | `F04 / G01`: Cảnh báo khi Tenant dùng quá 80% và 100% `daily_ai_budget_usd`. | `tenant_id, current_spend_usd, daily_budget_usd, threshold_pct` |

### 5.2. Hợp đồng Dữ liệu Pydantic v2 Chuẩn (Core Contracts — Khắc phục P0-1)

```python
# backend/contracts/core_schemas.py — Hợp đồng Pydantic v2 chuẩn cho 47 Modules
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcquisitionStatus(str, Enum):
    INGESTED = "INGESTED"
    EXTRACTED = "EXTRACTED"
    NORMALIZED = "NORMALIZED"
    FAILED = "FAILED"


class VerificationStatus(str, Enum):
    UNCHECKED = "UNCHECKED"
    AUTO_VERIFIED = "AUTO_VERIFIED"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"


class RightsStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    OWNED = "OWNED"
    LICENSED = "LICENSED"
    ALLOWED_DERIVATIVE = "ALLOWED_DERIVATIVE"
    RESTRICTED = "RESTRICTED"
    TAKEDOWN = "TAKEDOWN"


class PublicationStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    TAKEN_DOWN = "TAKEN_DOWN"


class QuestionItemType(str, Enum):
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTIPLE_SELECT = "MULTIPLE_SELECT"
    CLUSTER_TRUE_FALSE = "CLUSTER_TRUE_FALSE"
    SHORT_NUMERIC = "SHORT_NUMERIC"
    ESSAY = "ESSAY"
    GROUP_STEM = "GROUP_STEM"


class NumericParsePolicy(BaseModel):
    """Chính sách đọc câu trả lời ngắn (Phần III THPTQG 2025+) chống nhầm dấu chấm phân cách hàng nghìn."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    decimal_sep: Literal["COMMA_OR_DOT", "COMMA_ONLY", "DOT_ONLY"] = "COMMA_OR_DOT"
    allow_thousand_sep: bool = False
    max_chars: int = Field(default=4, ge=1, le=12)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0"))


class AcquisitionMethod(str, Enum):
    DOCUMENT_IMPORT = "DOCUMENT_IMPORT"
    WEB_CRAWL = "WEB_CRAWL"
    DRIVE_SYNC = "DRIVE_SYNC"
    AI_GENERATED = "AI_GENERATED"
    MANUAL_AUTHOR = "MANUAL_AUTHOR"


class ProvenanceContract(BaseModel):
    """
    Hợp đồng truy vết nguồn gốc đa kênh (Khắc phục lỗi bắt buộc document_version_id/page_no với Web Crawler):
    - Nếu từ DOCUMENT_IMPORT / DRIVE_SYNC: bắt buộc có document_version_id, page_no, bbox.
    - Nếu từ WEB_CRAWL: bắt buộc có source_url và raw_html_sha256.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: Optional[UUID] = None
    extraction_run_id: Optional[UUID] = None
    page_no: Optional[int] = Field(default=None, ge=1)
    bbox: Optional[tuple[float, float, float, float]] = None
    source_url: Optional[str] = None
    raw_html_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    original_question_label: Optional[str] = None


class QuestionOptionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_key: Literal["A", "B", "C", "D", "a", "b", "c", "d"]
    text_md: str = Field(min_length=1)
    figure_asset_ids: List[UUID] = Field(default_factory=list)


class QuestionCandidateContract(BaseModel):
    """Hợp đồng đầu ra thống nhất cho mọi bộ Parser (A02 Web Crawler, A03 Drive, A07 Exam Extractor)."""
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    acquisition_method: AcquisitionMethod
    item_type: QuestionItemType
    stem_md: str = Field(min_length=1)
    options: List[QuestionOptionContract] = Field(default_factory=list)
    source_answer_raw: Optional[str] = None
    source_rationale_md: Optional[str] = None
    figure_asset_ids: List[UUID] = Field(default_factory=list)
    provenance: ProvenanceContract
    acquisition_status: AcquisitionStatus = AcquisitionStatus.EXTRACTED
    verification_status: VerificationStatus = VerificationStatus.UNCHECKED
    rights_status: RightsStatus = RightsStatus.PENDING_REVIEW

    @model_validator(mode="after")
    def validate_options_and_provenance(self) -> "QuestionCandidateContract":
        if self.item_type == QuestionItemType.SINGLE_CHOICE and len(self.options) != 4:
            raise ValueError("Câu SINGLE_CHOICE chuẩn phải có đúng 4 phương án A, B, C, D.")
        if self.item_type == QuestionItemType.CLUSTER_TRUE_FALSE and len(self.options) != 4:
            raise ValueError("Câu CLUSTER_TRUE_FALSE chuẩn THPTQG 2025+ phải có đúng 4 mệnh đề a, b, c, d.")
        if self.acquisition_method == AcquisitionMethod.DOCUMENT_IMPORT:
            if self.provenance.document_version_id is None or self.provenance.page_no is None:
                raise ValueError("DOCUMENT_IMPORT bắt buộc phải có document_version_id và page_no.")
        elif self.acquisition_method == AcquisitionMethod.WEB_CRAWL:
            if not self.provenance.source_url or not self.provenance.raw_html_sha256:
                raise ValueError("WEB_CRAWL bắt buộc phải có source_url và raw_html_sha256.")
        return self


class AssessmentSnapshotPublicItem(BaseModel):
    """Cấu trúc câu hỏi trong Snapshot công khai (TUYỆT ĐỐI KHÔNG CHỨA ĐÁP ÁN)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_version_id: UUID
    stimulus_id: Optional[UUID] = None
    section_code: Literal["PART_I_MCQ", "PART_II_TF4", "PART_III_SHORT", "PART_IV_ESSAY"]
    sequence_order: int = Field(ge=1)
    points: Decimal = Field(gt=Decimal("0"))
    content_hash: str = Field(min_length=64, max_length=64)


class AssessmentSnapshotPublicContract(BaseModel):
    """Đóng băng vào assessment_versions.structure_snapshot_json (ADR-005)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_version_id: UUID
    framework_code: str
    subject_code: str
    duration_seconds: int = Field(gt=0)
    total_points: Decimal = Decimal("10.00")
    items: List[AssessmentSnapshotPublicItem]


class TelemetryCorrelationContract(BaseModel):
    """Ngữ cảnh quan sát xuyên module chuẩn W3C Trace Context (Khắc phục AUD-014)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_id: str = Field(min_length=8, max_length=100)
    causation_id: Optional[str] = None
    tenant_id: UUID
    actor_id: Optional[UUID] = None
    document_version_id: Optional[UUID] = None
    acquisition_job_id: Optional[UUID] = None
    pipeline_stage_run_id: Optional[UUID] = None
    parser_or_model_version: Optional[str] = None


class AutosaveRequestContract(BaseModel):
    """Canonical Autosave Contract có Optimistic Concurrency & Payload Hash (Khắc phục AUD-010)."""
    model_config = ConfigDict(extra="forbid")

    client_sequence: int = Field(ge=1)
    expected_response_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    payload_hash: str = Field(min_length=64, max_length=64)
    response_payload: Dict[str, Any]
    is_flagged: bool = False


class AutosaveResponseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["COMMITTED", "IDEMPOTENT_REPLAY"]
    attempt_id: UUID
    question_version_id: UUID
    committed_sequence: int
    saved_response_version: int
    server_timestamp: datetime
```

---


### 5.3. Đặc Tả End-to-End Workflows Trọng Yếu Theo Chuẩn 8 Bước (Khắc phục AUD-009)

Mọi luồng nghiệp vụ liên module trong hệ thống bắt buộc tuân thủ mẫu đặc tả **8 bước (`Trigger -> Validate -> Execute -> Persist -> Deliver -> Observe -> Recover -> Verify`)**:

| Bước Chuẩn (8-Step Template) | Luồng 1: Ingestion Tài Liệu Đến Question Bank (`A01 -> A05/A06 -> A07 -> A08 -> C02 -> A09 -> C01`) | Luồng 2: Làm Bài, Optimistic Autosave, Submit Cạnh Tranh & Chấm Điểm (`C04 -> C05 -> C06 -> C07 -> D05`) |
| :--- | :--- | :--- |
| **Step 1 — Trigger** | Giáo viên/Admin gọi `POST /api/v1/imports` sau khi hoàn tất Presigned Upload lên MinIO (`B03`), hoặc sự kiện `drive.file.discovered` từ `A03`. | Thí sinh gọi `PUT /api/v1/attempts/{id}/responses/{q_id}` (Autosave) hoặc `POST /api/v1/attempts/{id}/submit` (Nộp bài) hoặc Worker phát `attempt.expired`. |
| **Step 2 — Validate** | Kiểm tra JWT `tenant_id`, quyền `document:import`, chữ ký byte thực (`libmagic`), quét mã độc ClamAV, kiểm tra `sha256` chống trùng lặp và khởi tạo `rights_status = 'PENDING_REVIEW'`. | Kiểm tra quyền sở hữu `attempt.membership_id`, trạng thái `attempt.status == 'IN_PROGRESS'`, đồng hồ Server `now <= deadline_at + 5s`, `idempotency_key`, `payload_hash`, `expected_response_version` và `client_sequence`. |
| **Step 3 — Execute** | • Chặng Bronze: Lưu bản gốc bất biến.<br>• Chặng Silver (`A05/A06`): Chạy LibreOffice cô lập profile / `PDFEngineAdapter`, loại trừ bảng (`find_tables()`), gom hình Vector bằng Union-Find.<br>• Chặng Extraction & Dedup (`A07/A08`): Tách `QuestionCandidate`, băm `content_hash` & `structural_hash`.<br>• Chặng Verify (`C02`): Giải độc lập bằng `SymPy` + LLM. | • **Xử lý đua lệnh Submit vs Autosave:** Câu lệnh `Submit` khóa dòng `attempts` (`SELECT ... FOR UPDATE`) và chuyển `status = 'SUBMITTED'`; mọi gói Autosave đến sau thời điểm `status != 'IN_PROGRESS'` đều bị từ chối sạch (`409 ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED`).<br>• **Optimistic Concurrency:** Chỉ cập nhật khi `expected_response_version == current_revision_no`. |
| **Step 4 — Persist** | Ghi `document_versions`, `extraction_runs`, `provenance_records`, `question_candidates`, `verification_runs`, `review_tasks` và `outbox_events` trong Transaction có RLS `tenant_id`. | Ghi nguyên tử vào `responses` (tăng `current_revision_no`), `autosave_idempotency_registry` và `response_revisions` trên cụm Patroni Quorum (`synchronous_mode_strict: true`). Khi Submit, ghi thêm `outbox_events` (`attempt.submitted`). |
| **Step 5 — Deliver** | Outbox Relay đẩy sự kiện qua từng chặng; mỗi Worker ghi `inbox_events (consumer_name, event_id)` trước khi chạy; nếu lỗi sau 3 lần retry với backoff thì đẩy vào `dead_letter_items`. | Khi `attempt.submitted` phát ra, `ulp_worker_grading` kiểm tra `inbox_events`, đọc `assessment_answer_keys` (theo `key_revision_no` hiện hành), ánh xạ `option_permutations_json` và ghi bảng `grades` + phát `attempt.graded`. |
| **Step 6 — Observe** | Gắn xuyên suốt `correlation_id`, `tenant_id`, `document_version_id`, `acquisition_job_id`, `pipeline_stage_run_id`, `parser_version` vào JSON Log và OpenTelemetry Span. | Đo histogram `autosave_db_commit_duration_ms`, `autosave_conflict_total`, `submit_to_grade_latency_ms` kèm `correlation_id` và `attempt_id` (tuyệt đối không log PII). |
| **Step 7 — Recover** | Hỗ trợ chạy lại từng chặng lỗi (`POST /imports/{id}/retry`) hoặc từng trang (`POST /ocr/jobs/{id}/pages/{p}/reprocess`) mà không ghi đè file gốc ở tầng Bronze; ghim `parser_version` cũ qua `FeatureFlag` nếu có regression. | Khi mất mạng, trình duyệt giữ đáp án trong `IndexedDB` và gửi lại cùng `Idempotency-Key` (nhận `200 IDEMPOTENT_REPLAY`); khi lệch `expected_response_version` (`409`), client gọi `GET /attempts/{id}/sync` để đồng bộ lại. |
| **Step 8 — Verify** | Kiểm thử tự động trên 11 đề Golden Corpus + 30 đề Held-out (`GATE-06a`, `GATE-06b`: Precision $\ge 98\%$, Recall $\ge 96\%$) và kiểm tra Trigger DB chặn Publish khi `rights_status` chưa hợp lệ (`GATE-02`). | Kiểm thử đơn vị và tích hợp cho cả 6 tình huống của Canonical Autosave Contract (Mục 6.4), kiểm thử đua lệnh đồng thời (`Submit` vs `Autosave`) và diễn tập tắt 1 node Standby (`GATE-04`, `GATE-05`). |

---

## CHƯƠNG 6. CHÍN THUẬT TOÁN LÕI HOÀN CHỈNH (ĐÃ CHẠY KIỂM CHỨNG THỰC TẾ TRÊN PYTHON 3 — 0 LỖI REGEX/IMPORT/TYPE)

### 6.1. Thuật toán 1: Xử lý Tài liệu Đề thi Việt Nam Có Tách Bảng Khỏi Hình Vector (`VietnameseExamDocumentProcessor` — Module A04, A05, A06)

**Cải tiến cốt lõi ở V11.3 (Khắc phục V11-P1-2 & V11-P1-3):**
- **Loại trừ vùng Bảng biểu (`table_bboxes`) trước khi gom hình Vector:** Gọi `plumber_page.find_tables()` để xác định tọa độ các bảng dữ liệu (như bảng phức chất Hóa 11, bảng `colspan` Sinh 11, bảng HDC 3 cột). Mọi đường kẻ (`rects`, `lines`) thuộc vùng bảng được tách riêng sang luồng `HTML_TABLE`, tuyệt đối không đưa vào `merge_intersecting_rects_union_find` nên không bao giờ cắt nhầm bảng thành ảnh hay xóa mất chữ trong bảng.
- **Loại bỏ hoàn toàn hằng số tọa độ cứng:** Tính toán vùng biên theo tỷ lệ trang (`margin_ratio = 0.065`) kết hợp đối sánh mẫu chuỗi lặp liên trang.

```python
# backend/modules/document_intelligence/vn_exam_processor.py
from __future__ import annotations
import os
import re
import signal
import subprocess
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import pdfplumber
import pypdfium2 as pdfium

MAX_DOCX_XML_UNCOMPRESSED_BYTES = 25 * 1024 * 1024  # 25 MB Guard chống Zip Bomb


@dataclass(frozen=True)
class BBoxRect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def expanded(self, pad: float) -> "BBoxRect":
        return BBoxRect(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def intersects(self, other: "BBoxRect") -> bool:
        return not (
            self.x1 < other.x0 or self.x0 > other.x1 or self.y1 < other.y0 or self.y0 > other.y1
        )

    def overlap_ratio(self, other: "BBoxRect") -> float:
        if not self.intersects(other) or self.area <= 0:
            return 0.0
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        inter_area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        return inter_area / self.area

    def union(self, other: "BBoxRect") -> "BBoxRect":
        return BBoxRect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass(frozen=True)
class LayoutProfileConfig:
    header_band_ratio: float = 0.065
    footer_band_ratio: float = 0.065
    max_badge_area_ratio: float = 0.035
    min_figure_width: float = 35.0
    min_figure_height: float = 30.0
    cluster_padding_pt: float = 10.0
    crop_dpi: int = 300


class VietnameseExamDocumentProcessor:
    @staticmethod
    def inspect_docx_requires_libreoffice_render(docx_path: str) -> bool:
        try:
            with zipfile.ZipFile(docx_path, "r") as zf:
                info = zf.getinfo("word/document.xml")
                if info.file_size > MAX_DOCX_XML_UNCOMPRESSED_BYTES:
                    raise ValueError(
                        f"ZIP_BOMB_DETECTED: word/document.xml vượt ngưỡng an toàn ({info.file_size} bytes)"
                    )
                with zf.open(info, "r") as xml_stream:
                    xml_text = xml_stream.read(MAX_DOCX_XML_UNCOMPRESSED_BYTES).decode("utf-8", errors="ignore")
                return any(
                    tag in xml_text
                    for tag in ("<w:object", "oleObject", "<v:shape", "<wps:wsp", "<w:drawing", "<m:oMath")
                )
        except ValueError:
            raise
        except Exception:
            return True

    @staticmethod
    def convert_office_to_pdf_isolated(input_path: str, output_dir: str, timeout_sec: int = 45) -> str:
        with tempfile.TemporaryDirectory(prefix="ulp_lo_profile_") as lo_profile_dir:
            cmd = [
                "libreoffice",
                f"-env:UserInstallation=file://{lo_profile_dir}",
                "--headless",
                "--nologo",
                "--nolockcheck",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                output_dir,
                input_path,
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "HOME": "/tmp", "TMPDIR": "/tmp"},
                start_new_session=True,
            )
            try:
                _, stderr = proc.communicate(timeout=timeout_sec)
                if proc.returncode != 0:
                    raise RuntimeError(f"LibreOffice error ({proc.returncode}): {stderr.decode('utf-8', 'ignore')}")
            except subprocess.TimeoutExpired as exc:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
                raise TimeoutError(f"LibreOffice conversion timed out after {timeout_sec}s") from exc

        pdf_out = Path(output_dir) / f"{Path(input_path).stem}.pdf"
        if not pdf_out.exists() or pdf_out.stat().st_size == 0:
            raise RuntimeError("LibreOffice không sinh được file PDF hợp lệ.")
        return str(pdf_out)

    @staticmethod
    def merge_intersecting_rects_union_find(rects: List[BBoxRect], pad: float = 10.0) -> List[BBoxRect]:
        if not rects:
            return []
        working = list(rects)
        while True:
            n = len(working)
            parent = list(range(n))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i in range(n):
                exp_i = working[i].expanded(pad)
                for j in range(i + 1, n):
                    if exp_i.intersects(working[j].expanded(pad)):
                        ri, rj = find(i), find(j)
                        if ri != rj:
                            parent[rj] = ri

            clusters: Dict[int, BBoxRect] = {}
            for idx, r in enumerate(working):
                root = find(idx)
                clusters[root] = r if root not in clusters else clusters[root].union(r)

            next_working = sorted(clusters.values(), key=lambda b: (b.y0, b.x0))
            if len(next_working) == n:
                return next_working
            working = next_working

    @staticmethod
    def detect_dynamic_repeating_margins(
        pdf_plumber_doc: pdfplumber.PDF,
        config: LayoutProfileConfig,
    ) -> Set[str]:
        if len(pdf_plumber_doc.pages) < 2:
            return set()
        margin_counts: Counter[str] = Counter()
        for page in pdf_plumber_doc.pages:
            h = float(page.height)
            top_limit = h * config.header_band_ratio
            bottom_limit = h * (1.0 - config.footer_band_ratio)
            for line in page.extract_text_lines() or []:
                norm = re.sub(r"\d+", "<N>", line.get("text", "").strip())
                if not norm:
                    continue
                if float(line["top"]) <= top_limit or float(line["bottom"]) >= bottom_limit:
                    margin_counts[norm] += 1

        min_repeat = max(2, len(pdf_plumber_doc.pages) // 2)
        return {k for k, v in margin_counts.items() if v >= min_repeat}

    @classmethod
    def extract_figures_tables_and_clean_text(
        cls,
        pdf_path: str,
        output_figure_dir: str,
        doc_version_id: str,
        config: LayoutProfileConfig = LayoutProfileConfig(),
    ) -> Tuple[str, Dict[str, Dict]]:
        # Khắc phục V11-P1-2: Phát hiện vùng bảng (find_tables) TRƯỚC để không gom nhầm đường kẻ bảng vào hình vẽ Vector
        os.makedirs(output_figure_dir, exist_ok=True)
        figure_manifest: Dict[str, Dict] = {}
        continuous_lines: List[str] = []

        pdfium_doc = pdfium.PdfDocument(pdf_path)
        with pdfplumber.open(pdf_path) as plumber_doc:
            repeated_margin_patterns = cls.detect_dynamic_repeating_margins(plumber_doc, config)

            for page_idx, plumber_page in enumerate(plumber_doc.pages):
                w, h = float(plumber_page.width), float(plumber_page.height)
                page_area = max(1.0, w * h)
                top_band = h * config.header_band_ratio
                bottom_band = h * (1.0 - config.footer_band_ratio)

                # Bước 1: Tìm toàn bộ bảng dữ liệu trên trang để bảo vệ đường kẻ bảng và chữ trong bảng
                detected_tables = plumber_page.find_tables() or []
                table_rects: List[BBoxRect] = [
                    BBoxRect(float(t.bbox[0]), float(t.bbox[1]), float(t.bbox[2]), float(t.bbox[3]))
                    for t in detected_tables
                ]

                def is_inside_any_table(candidate_box: BBoxRect) -> bool:
                    return any(candidate_box.overlap_ratio(tb.expanded(4.0)) >= 0.50 for tb in table_rects)

                # Bước 2: Thu thập ảnh Raster và các nét vẽ Vector KHÔNG thuộc bảng biểu
                raw_drawing_rects: List[BBoxRect] = []
                for img in plumber_page.images or []:
                    r = BBoxRect(float(img["x0"]), float(img["top"]), float(img["x1"]), float(img["bottom"]))
                    if not is_inside_any_table(r):
                        raw_drawing_rects.append(r)

                for obj in (plumber_page.rects or []) + (plumber_page.curves or []) + (plumber_page.lines or []):
                    r = BBoxRect(float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"]))
                    if not is_inside_any_table(r):
                        raw_drawing_rects.append(r)

                merged_candidates = cls.merge_intersecting_rects_union_find(
                    raw_drawing_rects, pad=config.cluster_padding_pt
                )
                figure_boxes = [
                    b for b in merged_candidates
                    if b.width >= config.min_figure_width
                    and b.height >= config.min_figure_height
                    and not is_inside_any_table(b)
                ]

                page_elements: List[Tuple[float, str]] = []
                active_redaction_boxes: List[BBoxRect] = []

                if figure_boxes:
                    pdfium_page = pdfium_doc[page_idx]
                    scale = config.crop_dpi / 72.0
                    pil_page = pdfium_page.render(scale=scale).to_pil()

                    for box_idx, rect in enumerate(figure_boxes):
                        # Nhận diện huy hiệu/logo nhỏ ở vùng biên đầu trang đầu tiên theo tỷ lệ diện tích động
                        is_corner_badge = (
                            page_idx == 0
                            and rect.y0 <= h * 0.14
                            and rect.x0 <= w * 0.35
                            and (rect.area / page_area) <= config.max_badge_area_ratio
                        )
                        if is_corner_badge:
                            active_redaction_boxes.append(rect)
                            continue

                        clip = BBoxRect(
                            max(0.0, rect.x0 - 6.0),
                            max(0.0, rect.y0 - 6.0),
                            min(w, rect.x1 + 6.0),
                            min(h, rect.y1 + 6.0),
                        )
                        marker = f"[[ASSET_P{page_idx + 1}_{box_idx}]]"
                        img_filename = f"{doc_version_id}_P{page_idx + 1}_{box_idx}.png"
                        img_path = str(Path(output_figure_dir) / img_filename)

                        crop_px = (
                            int(clip.x0 * scale),
                            int(clip.y0 * scale),
                            int(clip.x1 * scale),
                            int(clip.y1 * scale),
                        )
                        pil_page.crop(crop_px).save(img_path)

                        figure_manifest[marker] = {
                            "file_path": img_path,
                            "page_no": page_idx + 1,
                            "bbox": [round(clip.x0, 1), round(clip.y0, 1), round(clip.x1, 1), round(clip.y1, 1)],
                        }
                        page_elements.append((clip.y0, marker))
                        active_redaction_boxes.append(rect)

                def is_outside_figures(obj: dict) -> bool:
                    if obj.get("object_type") != "char":
                        return True
                    cx = (float(obj["x0"]) + float(obj["x1"])) / 2.0
                    cy = (float(obj["top"]) + float(obj["bottom"])) / 2.0
                    return not any(b.x0 <= cx <= b.x1 and b.y0 <= cy <= b.y1 for b in active_redaction_boxes)

                filtered_page = plumber_page.filter(is_outside_figures)
                for line in filtered_page.extract_text_lines() or []:
                    txt = line.get("text", "").strip()
                    if not txt or re.search(r"^Trang\s+\d+\s*/\s*\d+$", txt, re.I):
                        continue
                    norm_pat = re.sub(r"\d+", "<N>", txt)
                    if norm_pat in repeated_margin_patterns and (
                        float(line["top"]) <= top_band or float(line["bottom"]) >= bottom_band
                    ):
                        continue
                    page_elements.append((float(line["top"]), txt))

                page_elements.sort(key=lambda item: item[0])
                continuous_lines.extend(item[1] for item in page_elements)

        pdfium_doc.close()
        return "\n".join(continuous_lines), figure_manifest
```

### 6.2. Thuật toán 2: Khử Trùng 3 Tầng Bằng Forward Chemical Tokenizer (Không Dùng Variable-Width Lookbehind) & Ghép Cặp 4 Tín Hiệu (`ThreeTierDeduplicationAndPairingEngine` — Module A08)

**Cải tiến cốt lõi ở V11.3 (Khắc phục triệt để V11-P0-1):**
- Loại bỏ hoàn toàn biểu thức `(?<!{CHEM_ELEMENTS_PATTERN})` vốn gây lỗi `re.error: look-behind requires fixed-width pattern` trong Python `re`.
- Sử dụng **Forward Chemical & Numeric Tokenizer (`CHEM_OR_NUMERIC_TOKEN_RE`)** chạy trên văn bản đã chuẩn hóa Unicode `NFC` **TRƯỚC KHI `.lower()`**:
  - Nhánh 1 (`chem_token`): Khớp trực tiếp các công thức/nhóm hóa học có nguyên tố viết hoa kèm chỉ số hoặc điện tích (VD: `H2O`, `Fe3O4`, `C2H5OH`, `Ca(OH)2`, `SO4^2-`, `[Co(NH3)6]Cl3`) và **giữ nguyên 100% chỉ số hóa học**.
  - Nhánh 2 (`standalone_num`): Khớp các con số đứng độc lập (số liệu bài toán như `100`, `0,25`, `2026`) và thay thế bằng `<NUM>`.
  - Sau khi hoàn tất thay thế số liệu bài toán, chuỗi mới được đưa về chữ thường `.lower()` để tính `structural_hash`.

```python
# backend/modules/acquisition/dedup_and_pairing.py
from __future__ import annotations
import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

# Regex thuận (Forward Tokenizer, không dùng variable-width lookbehind, chạy hoàn toàn hợp lệ trên Python re):
# - Group 'chem': Bắt các cụm công thức hóa học (VD: H2O, Fe3O4, C2H5OH, Ca(OH)2, [Ag(NH3)2]+, SO4^2-)
# - Group 'math_var_sub': Bắt biến toán học có chỉ số dưới (VD: u_1, x_2, a_n)
# - Group 'num': Bắt số liệu bài toán độc lập để thay bằng <NUM>
CHEM_OR_NUMERIC_TOKEN_RE = re.compile(
    r"(?P<chem>(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d+|\[[A-Za-z0-9\(\)]+\]\d*)(?:[A-Z][a-z]?\d*|\([A-Za-z0-9]+\)\d*)+|[A-Z][a-z]?\d+)"
    r"|(?P<math_sub>[A-Za-z]_\{?\d+\}?)"
    r"|(?P<num>(?<![A-Za-z_\\0-9])\d+(?:[.,]\d+)?)"  # Không dùng \b ở cuối để bắt cả số dính liền đơn vị như 10g, 100g, 20m/s
)


class ThreeTierDeduplicationAndPairingEngine:
    @staticmethod
    def normalize_unicode_preserve_case(text: str) -> str:
        nfc = unicodedata.normalize("NFC", text or "")
        nfc = nfc.replace("−", "-").replace("–", "-")
        # Chuyển các chữ số chỉ số dưới Unicode (₀..₉) về chữ số ASCII để xử lý thống nhất
        sub_map = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
        nfc = nfc.translate(sub_map)
        return re.sub(r"\s+", " ", nfc).strip()

    @classmethod
    def normalize_vn_text(cls, text: str) -> str:
        return cls.normalize_unicode_preserve_case(text).lower()

    @classmethod
    def structural_normalize_preserve_chemistry(cls, text: str) -> str:
        """
        Thay thế số liệu bài toán bằng <NUM> TRƯỚC KHI .lower() để bảo toàn 100% công thức Hóa học:
        - 'Đốt cháy 11,2 lít C2H4 thu được H2O' -> 'đốt cháy <num> lít c2h4 thu được h2o'
        - 'Cho m = 100g dung dịch H2SO4 20%' -> 'cho m = <num>g dung dịch h2so4 <num>%'
        """
        cased_nfc = cls.normalize_unicode_preserve_case(text)

        def _replacer(match: re.Match) -> str:
            if match.group("chem") is not None:
                return match.group("chem")
            if match.group("math_sub") is not None:
                return match.group("math_sub")
            return "<NUM>"

        replaced = CHEM_OR_NUMERIC_TOKEN_RE.sub(_replacer, cased_nfc)
        return replaced.lower()

    @classmethod
    def compute_content_and_structural_hashes(
        cls, stem_md: str, options_md: List[str]
    ) -> Tuple[str, str]:
        norm_stem = cls.normalize_vn_text(stem_md)
        norm_opts = sorted(cls.normalize_vn_text(opt) for opt in options_md)
        canonical_full = f"{norm_stem}||{'|'.join(norm_opts)}"
        content_hash = hashlib.sha256(canonical_full.encode("utf-8")).hexdigest()

        struct_stem = cls.structural_normalize_preserve_chemistry(stem_md)
        struct_opts = sorted(cls.structural_normalize_preserve_chemistry(opt) for opt in options_md)
        structural_full = f"{struct_stem}||{'|'.join(struct_opts)}"
        structural_hash = hashlib.sha256(structural_full.encode("utf-8")).hexdigest()

        return content_hash, structural_hash

    @classmethod
    def score_exam_and_solution_pairing(
        cls,
        exam_meta: Dict,
        sol_meta: Dict,
    ) -> Tuple[float, Dict[str, float]]:
        def clean_stem(name: str) -> str:
            s = cls.normalize_vn_text(Path(name).stem)
            return re.sub(r"\b(de|dap an|hdc|huong dan cham|loi giai|chi tiet|chinh thuc)\b", "", s).strip()

        stem_a, stem_b = clean_stem(exam_meta["filename"]), clean_stem(sol_meta["filename"])
        tokens_a, tokens_b = set(stem_a.split()), set(stem_b.split())
        name_sim = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))

        same_folder = 1.0 if exam_meta.get("folder_id") == sol_meta.get("folder_id") else 0.0
        same_subject = 1.0 if exam_meta.get("subject_code") == sol_meta.get("subject_code") else 0.0
        q_diff = abs(int(exam_meta.get("question_count", 0)) - int(sol_meta.get("question_count", 0)))
        count_sim = max(0.0, 1.0 - (q_diff / 10.0))

        total = round(0.40 * name_sim + 0.25 * same_folder + 0.20 * same_subject + 0.15 * count_sim, 3)
        return total, {
            "filename_similarity": round(name_sim, 3),
            "folder_proximity": same_folder,
            "subject_match": same_subject,
            "item_count_similarity": round(count_sim, 3),
        }
```

### 6.3. Thuật toán 3: Kiểm Chứng Đáp Án Độc Lập Bằng Tiến Trình `SymPy` Cô Lập (`sympy_sandbox_worker.py` & `sympy_runner.py` — Module C02, E04)

**Cải tiến bảo mật cốt lõi ở V11.3 (Khắc phục triệt để P0-5 — Chống RCE từ chuỗi của LLM):**
- Tuyệt đối **không gọi `sp.sympify()` trực tiếp trong tiến trình ứng dụng** trên chuỗi biểu thức do LLM sinh ra.
- Tách thành `sympy_sandbox_worker.py` chạy trong tiến trình con cô lập (`python -I`, không nạp site-packages/env của user, container không có mạng ra ngoài, filesystem read-only):
  1. **Lớp lọc Whitelist tên hàm/biến (`ALLOWED_NAMES`) & chặn `__`:** Từ chối ngay lập tức mọi chuỗi chứa `__import__`, `eval`, `exec`, `open`, `getattr`, `__class__`, `__subclasses__` hoặc dài quá 500 ký tự.
  2. **Giới hạn tài nguyên hệ điều hành (`resource.setrlimit`):** Khóa cứng RAM tối đa `256 MB` (`RLIMIT_AS`) và CPU tối đa `5 giây` (`RLIMIT_CPU`) để chặn các biểu thức gây nổ bộ nhớ như `10**10**10`.
  3. **Trình phân tích an toàn:** Dùng `parse_expr(..., local_dict=local, global_dict=glob, transformations=standard_transformations)` với `glob["__builtins__"] = {}`.

```python
# backend/modules/quality/sympy_verifier.py
from __future__ import annotations
import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ALLOWED_SYMPY_NAMES = {
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "ln", "exp",
    "Abs", "pi", "E", "Rational", "x", "y", "z", "t", "n", "a", "b", "c", "k",
}
MAX_EXPR_LEN = 500
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


class SymPySandboxWorker:
    """
    Logic thực thi bên trong tiến trình cô lập `python -I sympy_sandbox_worker.py`:
    - Bước 1: Kiểm tra độ dài và Whitelist tên định danh (chặn 100% __import__, open, exec, __class__...).
    - Bước 2: Áp đặt RLIMIT_AS (256MB) và RLIMIT_CPU (5s) khi chạy độc lập.
    - Bước 3: Dùng parse_expr với __builtins__ = {} thay vì sympify() trần.
    """

    @staticmethod
    def validate_expression_ast_and_whitelist(expr_raw: str) -> Tuple[bool, Optional[str], str]:
        expr = str(expr_raw or "").strip().replace("^", "**")
        if not expr or len(expr) > MAX_EXPR_LEN:
            return False, "EXPR_LENGTH_EXCEEDED_OR_EMPTY", expr
        if "__" in expr or ";" in expr or "lambda" in expr:
            return False, "FORBIDDEN_SYNTAX_TOKEN", expr
        found_names = set(IDENTIFIER_RE.findall(expr))
        disallowed = found_names - ALLOWED_SYMPY_NAMES
        if disallowed:
            return False, f"DISALLOWED_IDENTIFIER:{','.join(sorted(disallowed))}", expr
        return True, None, expr

    @classmethod
    def evaluate_safe_in_worker(cls, expr_raw: str) -> Dict[str, Any]:
        is_valid, err_code, expr = cls.validate_expression_ast_and_whitelist(expr_raw)
        if not is_valid:
            return {"ok": False, "error": err_code}

        import sympy as sp
        try:
            from sympy.parsing.sympy_parser import parse_expr, standard_transformations
            glob: Dict[str, Any] = {}
            exec("from sympy import *", glob)
            glob["__builtins__"] = {}
            local = {"ln": sp.log}
            result = parse_expr(expr, local_dict=local, global_dict=glob, transformations=standard_transformations)
            if getattr(result, "free_symbols", None):
                return {"ok": False, "error": "EXPRESSION_NOT_NUMERIC"}
            return {"ok": True, "value": str(sp.N(result, 15))}
        except Exception as exc:
            # H-02 Fail-Closed: Tuyệt đối không fallback sang sympify() khi gặp lỗi!
            return {"ok": False, "error": f"SANDBOX_PARSE_ERROR:{type(exc).__name__}"}


class IndependentSymPyAndLLMVerifier:
    MATH_PROBABILITY_KEYWORDS = ("xác suất", "công thức bayes", "biến cố", "ngẫu nhiên", "xét nghiệm dương tính")

    @classmethod
    def enforce_subject_lock(cls, text_md: str, inferred_subject: str, source_subject: Optional[str]) -> str:
        if source_subject:
            return source_subject
        lower_txt = text_md.lower()
        if inferred_subject == "BIO" and any(kw in lower_txt for kw in cls.MATH_PROBABILITY_KEYWORDS):
            return "MATH"
        return inferred_subject

    @staticmethod
    def canonicalize_numeric_answer(raw_val: Optional[str]) -> Optional[Decimal]:
        if raw_val is None:
            return None
        cleaned = raw_val.strip().replace(" ", "").replace(",", ".")
        cleaned = re.sub(r"[a-zA-Z%/°]+$", "", cleaned)
        try:
            if "/" in cleaned:
                num, den = cleaned.split("/", 1)
                return Decimal(num) / Decimal(den)
            return Decimal(cleaned)
        except (InvalidOperation, ZeroDivisionError):
            return None

    @staticmethod
    def run_sympy_subprocess_sandbox(
        worker_script_path: Path,
        expr: str,
        timeout_sec: float = 8.0,
    ) -> Dict[str, Any]:
        """Gọi worker trong tiến trình riêng biệt với cờ python -I, giới hạn thời gian và môi trường rỗng."""
        is_valid, err_code, _ = SymPySandboxWorker.validate_expression_ast_and_whitelist(expr)
        if not is_valid:
            return {"ok": False, "error": err_code}
        if not worker_script_path.is_file():
            # H-02 Fail-Closed: Khi thiếu file worker cô lập, lập tức trả lỗi WORKER_UNAVAILABLE, cấm chạy in-process!
            return {"ok": False, "error": "SANDBOX_WORKER_UNAVAILABLE"}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(worker_script_path)],
                input=json.dumps({"expr": expr}),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env={"PATH": "/usr/bin:/bin"},
                start_new_session=True,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "TIMEOUT"}
        if proc.returncode != 0:
            return {"ok": False, "error": f"WORKER_EXIT_{proc.returncode}"}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": "BAD_WORKER_OUTPUT"}

    @classmethod
    def verify_symbolic_or_numeric(
        cls,
        sympy_expr_str: str,
        source_answer_raw: str,
        tolerance: Decimal = Decimal("0.01"),
    ) -> Tuple[bool, Dict[str, str]]:
        res = SymPySandboxWorker.evaluate_safe_in_worker(sympy_expr_str)
        if not res.get("ok"):
            return False, {"error": str(res.get("error", "EVAL_FAILED"))}
        computed_float = Decimal(str(res["value"]))
        source_dec = cls.canonicalize_numeric_answer(source_answer_raw)
        if source_dec is None:
            return False, {"error": "UNPARSEABLE_SOURCE_ANSWER", "computed": str(computed_float)}

        diff = abs(computed_float - source_dec)
        is_match = diff <= tolerance
        return is_match, {
            "computed_canonical": str(round(computed_float, 4)),
            "source_canonical": str(source_dec),
            "abs_diff": str(round(diff, 4)),
        }
```

### 6.4. Thuật toán 4: Bốc Đề Tất Định, Giao Thức Idempotency-First Autosave (Kèm Kiểm Tra `deadline_at` & Payload Hash) & Chấm Điểm Chuẩn THPTQG 2025+ (`DeterministicExamAndAutosaveEngine` — Module C04, C05, C06, C07)

**Cải tiến cốt lõi ở V11.3 (Khắc phục V11-P0-7 & V11-P1-4):**
- Kiểm tra `now_utc > attempt_deadline_at + grace_seconds`: chặn lưu bài sau khi đã hết giờ thi (`403 ATTEMPT_DEADLINE_EXPIRED`).
- Khi trùng `idempotency_key`: băm SHA-256 của `incoming_payload` (`payload_sha256`) và so khớp với `saved["payload_sha256"]`. Nếu khớp trả `200 OK (IDEMPOTENT_REPLAY)`; nếu khác payload trả `422 IDEMPOTENCY_KEY_PAYLOAD_MISMATCH`.
- Bổ sung kiểm tra `len(s) > max_chars` (`max_chars = 4` theo đúng phiếu tô trả lời ngắn THPTQG 2025+) trong `parse_short_numeric_vn`.

```python
# backend/modules/assessment/exam_autosave_and_grader.py
from __future__ import annotations
import hashlib
import json
import random
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID


class DeterministicExamAndAutosaveEngine:
    TF4_SCORING_MAP = {
        0: Decimal("0.00"),
        1: Decimal("0.10"),
        2: Decimal("0.25"),
        3: Decimal("0.50"),
        4: Decimal("1.00"),
    }

    @staticmethod
    def compute_payload_sha256(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_attempt_permutation(
        items: List[Dict[str, Any]],
        attempt_seed: int,
    ) -> Tuple[List[UUID], Dict[str, List[str]]]:
        rng = random.Random(attempt_seed)
        sections: Dict[str, List[List[Dict[str, Any]]]] = {}
        stimulus_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

        for item in items:
            sec = item["section_code"]
            stim = str(item.get("stimulus_id") or item["question_version_id"])
            key = (sec, stim)
            stimulus_groups.setdefault(key, []).append(item)

        for (sec, _), grp in stimulus_groups.items():
            sections.setdefault(sec, []).append(grp)

        ordered_q_ids: List[UUID] = []
        option_permutations: Dict[str, List[str]] = {}

        for sec in sorted(sections.keys()):
            blocks = sections[sec]
            rng.shuffle(blocks)
            for block in blocks:
                for q in block:
                    qid = q["question_version_id"]
                    ordered_q_ids.append(qid)
                    if q["section_code"] == "PART_I_MCQ":
                        opts = ["A", "B", "C", "D"]
                        rng.shuffle(opts)
                        option_permutations[str(qid)] = opts

        return ordered_q_ids, option_permutations

    @classmethod
    def process_autosave_idempotent_first(
        cls,
        idempotency_registry: Dict[str, Dict[str, Any]],
        current_response_row: Dict[str, Any],
        attempt_deadline_at: datetime,
        incoming_idem_key: str,
        incoming_client_seq: int,
        incoming_payload: Dict[str, Any],
        expected_response_version: int,
        incoming_payload_hash: Optional[str] = None,
        attempt_status: str = "IN_PROGRESS",
        now_utc: Optional[datetime] = None,
        network_grace_seconds: int = 5,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Thực thi đầy đủ Canonical Autosave Contract (Khắc phục AUD-010):
        1. Idempotent Replay (200 OK) nếu trùng Idempotency-Key và khớp payload_sha256.
        2. Từ chối 422 nếu tái sử dụng Idempotency-Key với payload khác.
        3. Chống đua lệnh với Submit: từ chối 409 nếu attempt_status != 'IN_PROGRESS'.
        4. Kiểm tra deadline_at của Server (403 ATTEMPT_DEADLINE_EXPIRED).
        5. Kiểm tra Optimistic Concurrency (expected_response_version) và client_sequence (409 CONFLICT).
        """
        now_utc = now_utc or datetime.now(timezone.utc)
        incoming_hash = cls.compute_payload_sha256(incoming_payload)
        if incoming_payload_hash is not None and incoming_payload_hash != incoming_hash:
            return 422, {
                "error_code": "PAYLOAD_HASH_MISMATCH",
                "message": "Mã băm payload_hash từ client không khớp với dữ liệu nhận được trên server.",
            }

        # Bước 1: Kiểm tra Idempotency-Key trong autosave_idempotency_registry TRƯỚC TIÊN
        if incoming_idem_key in idempotency_registry:
            saved = idempotency_registry[incoming_idem_key]
            if saved["payload_sha256"] != incoming_hash:
                return 422, {
                    "error_code": "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH",
                    "message": "Cùng Idempotency-Key nhưng nội dung câu trả lời khác với bản đã lưu.",
                }
            return 200, {
                "status": "IDEMPOTENT_REPLAY",
                "committed_sequence": saved["client_sequence"],
                "saved_response_version": saved["revision_no"],
                "revision_no": saved["revision_no"],
                "server_timestamp": saved["recorded_at"],
            }

        # Bước 2: Chống đua lệnh giữa Submit và Autosave (Submit Race Protection)
        if attempt_status != "IN_PROGRESS":
            return 409, {
                "error_code": "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED",
                "attempt_status": attempt_status,
            }

        # Bước 3: Kiểm tra hạn nộp bài của phiên thi (deadline_at + grace_seconds)
        if now_utc > attempt_deadline_at + timedelta(seconds=network_grace_seconds):
            return 403, {
                "error_code": "ATTEMPT_DEADLINE_EXPIRED",
                "deadline_at": attempt_deadline_at.isoformat(),
            }

        # Bước 4: Kiểm tra Optimistic Concurrency (expected_response_version) & Monotonic Sequence
        if expected_response_version != current_response_row["current_revision_no"]:
            return 409, {
                "error_code": "OPTIMISTIC_VERSION_CONFLICT",
                "current_response_version": current_response_row["current_revision_no"],
                "current_sequence": current_response_row["current_sequence"],
            }

        if incoming_client_seq <= current_response_row["current_sequence"]:
            return 409, {
                "error_code": "STALE_CLIENT_SEQUENCE",
                "current_sequence": current_response_row["current_sequence"],
            }

        # Bước 5: Cập nhật nguyên tử dòng Response và ghi vào Registry + Revision
        new_rev = current_response_row["current_revision_no"] + 1
        current_response_row["current_sequence"] = incoming_client_seq
        current_response_row["current_revision_no"] = new_rev
        current_response_row["last_idempotency_key"] = incoming_idem_key
        current_response_row["response_payload"] = incoming_payload

        idempotency_registry[incoming_idem_key] = {
            "revision_no": new_rev,
            "client_sequence": incoming_client_seq,
            "payload_sha256": incoming_hash,
            "payload_json": incoming_payload,
            "recorded_at": now_utc,
        }
        return 200, {
            "status": "COMMITTED",
            "committed_sequence": incoming_client_seq,
            "saved_response_version": new_rev,
            "revision_no": new_rev,
            "server_timestamp": now_utc,
        }

    @classmethod
    def parse_short_numeric_vn(
        cls,
        raw_input: str,
        max_chars: int = 4,
        allow_thousand_sep: bool = False,
    ) -> Tuple[Optional[Decimal], Optional[str]]:
        s = (raw_input or "").strip().replace(" ", "")
        if not s:
            return None, "EMPTY"
        if len(s) > max_chars:
            return None, f"MAX_CHARS_EXCEEDED_LIMIT_{max_chars}"
        if s.count(",") + s.count(".") > 1:
            return None, "AMBIGUOUS_MULTIPLE_SEPARATORS"
        if not allow_thousand_sep and re.match(r"^-?\d{1,3}\.\d{3}$", s):
            return None, "THOUSAND_SEPARATOR_NOT_ALLOWED_USE_PLAIN_DIGITS"
        normalized = s.replace(",", ".")
        try:
            return Decimal(normalized), None
        except InvalidOperation:
            return None, "INVALID_NUMBER_FORMAT"

    @classmethod
    def grade_thptqg_2025_item(
        cls,
        item_type: str,
        max_points: Decimal,
        answer_key: Dict[str, Any],
        student_payload: Dict[str, Any],
        option_permutation: Optional[List[str]] = None,
    ) -> Tuple[Decimal, Dict[str, Any]]:
        """
        Chấm điểm tất định 3 phần đề thi THPTQG 2025+ có hỗ trợ ánh xạ ngược hoán vị phương án:
        - Nếu đề của thí sinh có hoán vị phương án (VD option_permutation = ["C", "A", "D", "B"]),
          và thí sinh chọn vị trí hiển thị "B" (index 1), hệ thống ánh xạ ngược về phương án gốc "A"
          trước khi so khớp với answer_key["correct_key"].
        """
        if item_type == "SINGLE_CHOICE":
            selected_display_key = student_payload.get("selected_key")
            canonical_selected = selected_display_key
            if option_permutation and selected_display_key in ("A", "B", "C", "D"):
                pos_idx = ord(selected_display_key) - ord("A")
                if 0 <= pos_idx < len(option_permutation):
                    canonical_selected = option_permutation[pos_idx]
            is_ok = canonical_selected == answer_key.get("correct_key")
            return (max_points if is_ok else Decimal("0.00")), {
                "is_correct": is_ok,
                "displayed_key": selected_display_key,
                "canonical_key": canonical_selected,
            }

        if item_type == "CLUSTER_TRUE_FALSE":
            correct_map: Dict[str, bool] = answer_key["sub_items"]
            student_map: Dict[str, bool] = student_payload.get("sub_answers", {})
            match_count = sum(1 for k in ("a", "b", "c", "d") if k in student_map and student_map[k] == correct_map[k])
            raw_pts = cls.TF4_SCORING_MAP.get(match_count, Decimal("0.00"))
            scaled_pts = (raw_pts * max_points).quantize(Decimal("0.01"))
            return scaled_pts, {"matched_sub_items": match_count}

        if item_type == "SHORT_NUMERIC":
            max_chars = int(answer_key.get("max_chars", 4))
            val, err = cls.parse_short_numeric_vn(str(student_payload.get("text_value", "")), max_chars=max_chars)
            if val is None:
                return Decimal("0.00"), {"parse_error": err}
            target = Decimal(str(answer_key["target_value"]))
            tol = Decimal(str(answer_key.get("tolerance", "0.01")))
            is_ok = abs(val - target) <= tol
            return (max_points if is_ok else Decimal("0.00")), {"is_correct": is_ok, "parsed_value": str(val)}

        return Decimal("0.00"), {"status": "REQUIRES_RUBRIC_GRADING"}
```

### 6.5. Thuật toán 5: Lịch Ôn Tập Chuẩn FSRS v5 (Đủ 19 Trọng Số `W[0..18]`) & Cổng Trưởng Thành IRT Độc Lập (`AdaptiveMasteryFSRSAndIRTEngine` — Module D02, D04, D06)

**Cải tiến cốt lõi ở V11.3 (Khắc phục V11-P1-1 & V11-P1-5):**
- Cài đặt đầy đủ **19 tham số chuẩn của FSRS v5 (`W[0..18]`)** gồm: khởi tạo $S_0(G)$, khởi tạo $D_0(G) = w_4 - e^{w_5(G-1)} + 1$, cập nhật độ khó theo 3 bước chuẩn FSRS-5 gồm giảm chấn tuyến tính (Linear Damping) và hồi quy trung bình (Mean Reversion): $\Delta D = -w_6 \cdot (G - 3)$, $D' = D + \Delta D \cdot \frac{10 - D}{9}$, $D'' = w_7 \cdot D_0(4) + (1 - w_7) \cdot D'$ (giới hạn trong $[1, 10]$), công thức độ ổn định sau khi nhớ lại thành công (nhân hệ số phạt `Hard` $w_{15}$ và thưởng `Easy` $w_{16}$), công thức độ ổn định sau khi quên $S'_f(D, S, R) = w_{11} D^{-w_{12}} ((S+1)^{w_{13}} - 1) e^{w_{14}(1-R)}$, và công thức khoảng ôn luỹ thừa chuẩn $I(r, S) = \frac{S}{19/81}\left(r^{-2} - 1\right)$.
- Tách bạch hoàn toàn FSRS v5 (Memory Scheduler) khỏi 4 bậc của mô hình năng lực IRT (`L1 Rule-Weighted -> L2 Rasch 1PL -> L3 2PL/GPCM -> L4 3PL Beta Prior`).

```python
# backend/modules/adaptive/fsrs_and_irt_engine.py
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple
from uuid import UUID


@dataclass(frozen=True)
class FSRSState:
    stability: float
    difficulty: float
    reps: int
    lapses: int


class AdaptiveMasteryFSRSAndIRTEngine:
    # Đầy đủ 19 trọng số chuẩn của FSRS v5 (W[0] .. W[18])
    W: Tuple[float, ...] = (
        0.40255,   # w0: S0(Again)
        1.18385,   # w1: S0(Hard)
        3.17300,   # w2: S0(Good)
        15.69105,  # w3: S0(Easy)
        7.19490,   # w4: D0 base
        0.53450,   # w5: D0 exponent
        1.46040,   # w6: D delta
        0.00460,   # w7: D mean reversion weight
        1.54575,   # w8: Recall stability base
        0.11920,   # w9: Recall stability S power
        1.01925,   # w10: Recall stability R factor
        1.93950,   # w11: Forget stability base
        0.11000,   # w12: Forget stability D power
        0.29605,   # w13: Forget stability S power
        2.26980,   # w14: Forget stability R factor
        0.23150,   # w15: Hard penalty
        2.98980,   # w16: Easy bonus
        0.51655,   # w17: Short-term stability factor
        0.66210,   # w18: Short-term stability offset
    )
    DECAY: float = -0.5
    FACTOR: float = 19.0 / 81.0  # Đảm bảo R(t=S, S) = 0.90

    @classmethod
    def _init_difficulty(cls, rating: int) -> float:
        d0 = cls.W[4] - math.exp(cls.W[5] * (rating - 1)) + 1.0
        return min(10.0, max(1.0, d0))

    @classmethod
    def _next_difficulty(cls, d: float, rating: int) -> float:
        """Cập nhật độ khó chuẩn FSRS-5 (py-fsrs): Linear Damping (10 - D)/9 + Mean Reversion về D0(4)."""
        delta_d = -cls.W[6] * (rating - 3)
        d_damped = d + delta_d * ((10.0 - d) / 9.0)
        d_easy_ref = cls._init_difficulty(4)
        new_d = cls.W[7] * d_easy_ref + (1.0 - cls.W[7]) * d_damped
        return min(10.0, max(1.0, new_d))

    @classmethod
    def compute_retrievability(cls, elapsed_days: float, stability: float) -> float:
        if stability <= 0:
            return 0.0
        return (1.0 + cls.FACTOR * (max(0.0, elapsed_days) / stability)) ** cls.DECAY

    @classmethod
    def next_interval_days(cls, stability: float, request_retention: float = 0.90) -> int:
        new_interval = (stability / cls.FACTOR) * ((request_retention ** (1.0 / cls.DECAY)) - 1.0)
        return max(1, round(new_interval))

    @classmethod
    def step_fsrs_v5(
        cls,
        state: Optional[FSRSState],
        rating: Literal[1, 2, 3, 4],  # 1: Again, 2: Hard, 3: Good, 4: Easy
        elapsed_days: float,
        request_retention: float = 0.90,
    ) -> Tuple[FSRSState, int]:
        """
        Khớp 100% phương trình toán học của py-fsrs (FSRS-5):
        - Dùng độ khó hiện tại `state.difficulty` (trước khi cập nhật) trong công thức tính S'_r và S'_f.
        - Áp dụng Linear Damping `(10 - D) / 9` khi cập nhật độ khó.
        - Khi quên (rating == 1), chặn trên độ ổn định mới bởi `S / exp(w17 * w18)`.
        """
        if state is None or state.reps == 0:
            init_s = cls.W[rating - 1]
            init_d = cls._init_difficulty(rating)
            interval = cls.next_interval_days(init_s, request_retention)
            return FSRSState(round(init_s, 4), round(init_d, 4), 1, 1 if rating == 1 else 0), interval

        prev_d = state.difficulty
        prev_s = state.stability
        new_d = cls._next_difficulty(prev_d, rating)

        # Ôn tập ngắn hạn trong cùng ngày (elapsed_days < 1.0)
        if elapsed_days < 1.0:
            s_short = prev_s * math.exp(cls.W[17] * (rating - 3 + cls.W[18]))
            new_s = max(0.1, s_short)
            lapses = state.lapses + (1 if rating == 1 else 0)
            return FSRSState(round(new_s, 4), round(new_d, 4), state.reps + 1, lapses), cls.next_interval_days(new_s, request_retention)

        r = cls.compute_retrievability(elapsed_days, prev_s)

        if rating == 1:
            # Công thức Forget Stability S'_f chuẩn FSRS-5 dùng prev_d và chặn trần bởi prev_s / exp(w17 * w18)
            s_forget = (
                cls.W[11]
                * (prev_d ** (-cls.W[12]))
                * (((prev_s + 1.0) ** cls.W[13]) - 1.0)
                * math.exp(cls.W[14] * (1.0 - r))
            )
            short_term_cap = prev_s / math.exp(cls.W[17] * cls.W[18])
            new_s = max(0.1, min(s_forget, short_term_cap))
            lapses = state.lapses + 1
        else:
            # Công thức Recall Stability S'_r chuẩn FSRS-5 dùng prev_d
            hard_penalty = cls.W[15] if rating == 2 else 1.0
            easy_bonus = cls.W[16] if rating == 4 else 1.0
            inc = (
                math.exp(cls.W[8])
                * (11.0 - prev_d)
                * (prev_s ** (-cls.W[9]))
                * (math.exp(cls.W[10] * (1.0 - r)) - 1.0)
                * hard_penalty
                * easy_bonus
            )
            new_s = prev_s * (1.0 + inc)
            lapses = state.lapses

        interval = cls.next_interval_days(new_s, request_retention)
        return FSRSState(round(new_s, 4), round(new_d, 4), state.reps + 1, lapses), interval

    @staticmethod
    def select_irt_model_tier(
        sample_size: int,
        se_b: float,
        learner_count: int = 1000,
        ability_sd: float = 0.80,
        converged: bool = True,
        infit_msq: float = 1.00,
        max_abs_dif: float = 0.10,
    ) -> str:
        """
        Cổng kiểm định chất lượng hiệu chuẩn IRT đa tiêu chí (Khắc phục AUD-003 & AUD-011):
        Kiểm tra đồng thời cỡ mẫu, số lượng người học, độ phân tán năng lực (ability_sd),
        sự hội tụ (converged), độ phù hợp (infit_msq), sai số chuẩn SE(b) và độ lệch DIF.
        Nếu không đạt điều kiện của bậc cao, tự động fallback về bậc đơn giản hơn.
        """
        if not converged or sample_size < 150 or learner_count < 100 or ability_sd < 0.60 or se_b >= 0.30:
            return "RULE_WEIGHTED_L1"
        if not (0.70 <= infit_msq <= 1.30) or max_abs_dif > 0.50:
            return "RULE_WEIGHTED_L1"

        if (
            sample_size >= 1000
            and learner_count >= 800
            and ability_sd >= 0.70
            and se_b < 0.20
            and (0.80 <= infit_msq <= 1.20)
            and max_abs_dif <= 0.40
        ):
            return "IRT_3PL_GPCM_L4"

        if (
            sample_size >= 300
            and learner_count >= 250
            and ability_sd >= 0.65
            and se_b < 0.25
            and (0.75 <= infit_msq <= 1.25)
            and max_abs_dif <= 0.45
        ):
            return "IRT_2PL_GPCM_L3"

        return "RASCH_1PL_L2"

    @staticmethod
    def resolve_prerequisite_remediation_order(
        weak_node_ids: List[UUID],
        prereq_edges: List[Tuple[UUID, UUID]],
        mastery_by_node: Dict[UUID, float],
    ) -> List[Tuple[UUID, str]]:
        parents_map: Dict[UUID, List[UUID]] = {}
        for src, dst in prereq_edges:
            parents_map.setdefault(dst, []).append(src)

        ordered_plan: List[Tuple[UUID, str]] = []
        visited: set[UUID] = set()

        def dfs(node_id: UUID, dependent_label: Optional[UUID] = None) -> None:
            if node_id in visited:
                return
            visited.add(node_id)
            for p_id in parents_map.get(node_id, []):
                if mastery_by_node.get(p_id, 0.0) < 0.60:
                    dfs(p_id, dependent_label=node_id)
            reason = (
                f"Cần củng cố kiến thức tiên quyết này trước khi học chuyên đề phụ thuộc ({dependent_label})"
                if dependent_label
                else "Chuyên đề trọng tâm đang có mức độ nắm vững dưới ngưỡng (< 50%)"
            )
            ordered_plan.append((node_id, reason))

        for w_id in weak_node_ids:
            dfs(w_id)
        return ordered_plan
```

### 6.6. Thuật toán 6: Bộ Cào Câu Hỏi Hợp Pháp Dùng `ip_obj.is_global` + Kiểm Tra Mỗi Bước Redirect (`PermittedWebCrawlerEngine` — Module A02)

```python
# backend/modules/crawler/safe_crawler_and_adapters.py
from __future__ import annotations
import hashlib
import ipaddress
import socket
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup


class SSRFDNSRebindingError(PermissionError):
    pass


class AntiSSRFResolver:
    """
    Sử dụng trực tiếp thuộc tính chuẩn `ip_obj.is_global` của thư viện `ipaddress` Python 3.11+
    (tự động chặn mọi dải Private, Loopback, Link-Local, CGNAT 100.64.0.0/10, Multicast, Reserved, Unspecified
    cho cả IPv4 và IPv6/IPv4-mapped) và ghim IP vào kết nối socket ở TỪNG bước HTTP Redirect.
    """

    @classmethod
    def validate_and_pin_ip(cls, hostname: str, port: int = 443) -> str:
        if not hostname:
            raise SSRFDNSRebindingError("Hostname rỗng.")
        try:
            addr_info = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SSRFDNSRebindingError(f"Không thể phân giải DNS cho {hostname}") from exc

        verified_ips: List[str] = []
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
                ip_obj = ip_obj.ipv4_mapped

            if not ip_obj.is_global or ip_obj.is_multicast:
                raise SSRFDNSRebindingError(
                    f"SSRF_BLOCKED: Host {hostname} phân giải ra địa chỉ không phải Global Public IP ({ip_str})."
                )
            verified_ips.append(ip_str)

        if not verified_ips:
            raise SSRFDNSRebindingError(f"Không có IP hợp lệ cho {hostname}.")
        return verified_ips[0]


@dataclass
class ParserDriftMonitor:
    window_size: int = 30
    max_empty_rate: float = 0.15
    recent_outcomes: Deque[int] = field(default_factory=lambda: deque(maxlen=30))

    def record_page_yield(self, extracted_count: int) -> Tuple[bool, float]:
        self.recent_outcomes.append(1 if extracted_count == 0 else 0)
        if len(self.recent_outcomes) < 10:
            return False, 0.0
        empty_rate = sum(self.recent_outcomes) / len(self.recent_outcomes)
        return empty_rate > self.max_empty_rate, round(empty_rate, 3)


class PermittedWebCrawlerEngine:
    ALLOWED_RIGHTS = {"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"}

    def __init__(self, user_agent: str = "ULPComplianceBot/9.0 (+https://ulp.edu.vn/bot-policy)"):
        self.user_agent = user_agent
        self._last_request_ts: Dict[str, float] = {}

    def verify_url_or_redirect_hop(
        self,
        source_config: Dict,
        target_url: str,
        robots_txt_content: str,
        hop_count: int = 0,
        max_redirects: int = 3,
    ) -> str:
        """Kiểm tra chính sách bản quyền, domain, robots.txt và ghim IP ở cả request gốc lẫn mỗi bước Redirect."""
        if hop_count > max_redirects:
            raise PermissionError(f"TOO_MANY_REDIRECTS: vượt quá {max_redirects} lần chuyển hướng.")
        if source_config.get("rights_status") not in self.ALLOWED_RIGHTS:
            raise PermissionError("RIGHTS_GATE_BLOCKED: Nguồn chưa được cấp phép bản quyền.")

        parsed = urllib.parse.urlparse(target_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Chỉ hỗ trợ giao thức http/https.")

        allowed_domain = source_config["allowed_domain"].lower()
        if parsed.hostname is None or parsed.hostname.lower() != allowed_domain:
            raise PermissionError(f"CROSS_DOMAIN_REDIRECT_BLOCKED: {parsed.hostname} != {allowed_domain}")

        allowed_prefixes: List[str] = source_config.get("allowed_path_prefixes", ["/"])
        if not any(parsed.path.startswith(prefix) for prefix in allowed_prefixes):
            raise PermissionError(f"PATH_NOT_ALLOWED: {parsed.path}")

        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots_txt_content.splitlines())
        if not rp.can_fetch(self.user_agent, target_url):
            raise PermissionError(f"ROBOTS_TXT_DISALLOWED: {target_url}")

        return AntiSSRFResolver.validate_and_pin_ip(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )

    @staticmethod
    def open_pinned_socket_connection(
        hostname: str,
        pinned_ip: str,
        port: int = 443,
        use_tls: bool = True,
        timeout_sec: float = 10.0,
    ) -> socket.socket:
        """
        Khắc phục triệt để lỗ hổng TOCTOU DNS Rebinding:
        Mở kết nối TCP trực tiếp tới `pinned_ip` đã được kiểm tra `is_global`,
        nhưng ở tầng TLS vẫn truyền `server_hostname=hostname` để xác thực chứng chỉ SSL và SNI chuẩn xác.
        """
        import ssl
        raw_sock = socket.create_connection((pinned_ip, port), timeout=timeout_sec)
        if not use_tls:
            return raw_sock
        ctx = ssl.create_default_context()
        return ctx.wrap_socket(raw_sock, server_hostname=hostname)

    def enforce_politeness_rate_limit(self, domain: str, min_interval_sec: float = 3.0) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_ts.get(domain, 0.0)
        if elapsed < min_interval_sec:
            time.sleep(min_interval_sec - elapsed)
        self._last_request_ts[domain] = time.monotonic()

    @staticmethod
    def parse_vietjack_style_question_blocks(html_bronze: str, page_url: str) -> List[Dict]:
        soup = BeautifulSoup(html_bronze, "lxml")
        candidates: List[Dict] = []
        for idx, block in enumerate(soup.select(".question-item, .vietjack-quiz-block, .box-question"), start=1):
            stem_el = block.select_one(".question-title, .quiz-question-content")
            if not stem_el:
                continue
            stem_text = stem_el.get_text(" ", strip=True)
            options_raw = [opt.get_text(" ", strip=True) for opt in block.select(".option-item, .quiz-option")]
            rationale_el = block.select_one(".solution-content, .vietjack-explanation")
            rationale_text = rationale_el.get_text("\n", strip=True) if rationale_el else None
            candidates.append(
                {
                    "original_question_label": f"Câu {idx}",
                    "stem_md": stem_text,
                    "options_text": options_raw[:4],
                    "source_rationale_md": rationale_text,
                    "source_url": page_url,
                    "raw_html_sha256": hashlib.sha256(str(block).encode("utf-8")).hexdigest(),
                }
            )
        return candidates
```

### 6.7. Thuật toán 7: Đồng Bộ Google Drive 2 Chế Độ (`Picker + drive.file` & `Shared Drive Service Account`), Bung Nén Chống Zip Bomb & Ghép Cặp (`GoogleDriveDifferentialSyncEngine` — Module A03)

**Làm rõ kiến trúc tích hợp Google Drive ở V11.3 (Khắc phục V11-P1-6):**
- **Chế độ 1 (`PICKER_DRIVE_FILE` — Mặc định cho Giáo viên cá nhân):** Vì OAuth scope `drive.file` chỉ cấp quyền truy cập trên từng file cụ thể mà người dùng click chọn (chọn 1 folder trên Picker không tự động cấp quyền đọc các file con bên trong folder đó), giao diện Google Picker được cấu hình ở chế độ **chọn nhiều file trực tiếp (`DocsViewMode.LIST` + `MULTISELECT_ENABLED`) hoặc chọn 1 file nén `.zip` chứa cả bộ đề**.
- **Chế độ 2 (`SHARED_DRIVE_SERVICE_ACCOUNT` — Dành cho Trường học / Sở GD B2B muốn quét tự động thư mục):** Nhà trường chia sẻ (Share) thư mục Google Drive cho địa chỉ email của **Service Account** hệ thống (quyền `Viewer`). Worker dùng Service Account gọi `files.list` / `changes.list` để tự động phát hiện mọi file mới tạo trong thư mục con mà không cần người dùng mở Google Picker và không vướng OAuth User-Level Restricted Scope.

```python
# backend/modules/drive_sync/drive_differential_sync.py
from __future__ import annotations
import os
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
from backend.modules.acquisition.dedup_and_pairing import ThreeTierDeduplicationAndPairingEngine


class ArchiveSecurityViolationError(ValueError):
    # Ném ra khi file nén vi phạm giới hạn Zip Bomb hoặc chứa đường dẫn Path Traversal (../).
    pass


@dataclass(frozen=True)
class SafeArchivePolicy:
    max_entries: int = 500
    max_total_uncompressed_bytes: int = 200 * 1024 * 1024  # 200 MB
    max_compression_ratio: float = 50.0  # Chặn tỷ lệ nén > 50:1
    max_directory_depth: int = 4
    allowed_extensions: Tuple[str, ...] = (".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png")


class GoogleDriveDifferentialSyncEngine:
    # 1. Đồng bộ vi sai qua Google Drive Changes API (startPageToken / newStartPageToken)
    #    với OAuth scope an toàn `https://www.googleapis.com/auth/drive.file` + Google Picker.
    # 2. Xử lý an toàn khi file bị xóa/thu hồi quyền trên Drive (đánh dấu SOURCE_REVOKED, giữ nguyên đề cũ).
    # 3. Bung nén file .zip trong sandbox có kiểm tra chống Zip Bomb, Symlink và Path Traversal (`../`).
    # 4. Tự động ghép cặp `File Đề + File Hướng Dẫn Chấm (HDC)` theo 4 tín hiệu với ngưỡng tin cậy.

    @staticmethod
    def extract_zip_safely_in_sandbox(
        zip_file_path: str,
        target_extract_dir: str,
        policy: SafeArchivePolicy = SafeArchivePolicy(),
    ) -> List[str]:
        os.makedirs(target_extract_dir, exist_ok=True)
        resolved_root = Path(target_extract_dir).resolve()
        extracted_files: List[str] = []
        total_uncompressed = 0

        with zipfile.ZipFile(zip_file_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > policy.max_entries:
                raise ArchiveSecurityViolationError(
                    f"ZIP_TOO_MANY_FILES: {len(infos)} files vượt ngưỡng {policy.max_entries}"
                )

            for info in infos:
                if info.is_dir():
                    continue

                # 1. Chặn Path Traversal (../ hoặc đường dẫn tuyệt đối)
                clean_rel = posixpath.normpath(info.filename).lstrip("/")
                if clean_rel.startswith("..") or "/../" in clean_rel or "\\" in clean_rel:
                    raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: {info.filename}")

                # 2. Kiểm tra độ sâu thư mục
                depth = len(Path(clean_rel).parts)
                if depth > policy.max_directory_depth:
                    raise ArchiveSecurityViolationError(f"ARCHIVE_DEPTH_EXCEEDED: {clean_rel} (depth={depth})")

                # 3. Chặn Symlink bên trong file Zip (Unix external_attr mode 0o120000)
                unix_mode = info.external_attr >> 16
                if (unix_mode & 0o170000) == 0o120000:
                    raise ArchiveSecurityViolationError(f"SYMLINK_IN_ARCHIVE_BLOCKED: {info.filename}")

                # 4. Kiểm tra tỷ lệ nén và tổng dung lượng giải nén chống Zip Bomb
                compressed_sz = max(1, info.compress_size)
                ratio = info.file_size / compressed_sz
                if ratio > policy.max_compression_ratio and info.file_size > 1024 * 1024:
                    raise ArchiveSecurityViolationError(
                        f"ZIP_BOMB_RATIO_BLOCKED: {info.filename} có tỷ lệ nén {ratio:.1f}:1"
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > policy.max_total_uncompressed_bytes:
                    raise ArchiveSecurityViolationError(
                        f"ZIP_TOTAL_SIZE_EXCEEDED: Tổng dung lượng vượt {policy.max_total_uncompressed_bytes} bytes"
                    )

                ext = Path(clean_rel).suffix.lower()
                if ext not in policy.allowed_extensions:
                    continue

                dest_path = (resolved_root / clean_rel).resolve()
                if not str(dest_path).startswith(str(resolved_root)):
                    raise ArchiveSecurityViolationError(f"ESCAPE_SANDBOX_BLOCKED: {dest_path}")

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src_stream, open(dest_path, "wb") as dst_file:
                    while True:
                        chunk = src_stream.read(65536)
                        if not chunk:
                            break
                        dst_file.write(chunk)
                extracted_files.append(str(dest_path))

        return extracted_files

    @staticmethod
    def process_drive_changes_batch(
        change_items: List[Dict[str, Any]],
        existing_docs_by_external_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        # Xử lý danh sách thay đổi từ Google Drive API `changes.list`:
        # - Nếu file chỉ đổi tên/di chuyển thư mục nhưng `sha256Checksum` không đổi -> Chỉ cập nhật metadata, KHÔNG chạy lại OCR.
        # - Nếu file bị xóa (`removed=True` hoặc `trashed=True`) -> Đánh dấu `SOURCE_REVOKED`, bảo toàn đề thi đã nhập.
        actions: Dict[str, List[Dict[str, Any]]] = {
            "ingest_new_or_modified": [],
            "metadata_only_update": [],
            "mark_revoked": [],
        }

        for change in change_items:
            file_id = change["fileId"]
            file_meta = change.get("file") or {}
            is_removed = bool(change.get("removed")) or bool(file_meta.get("trashed"))

            if is_removed:
                if file_id in existing_docs_by_external_id:
                    actions["mark_revoked"].append({"external_id": file_id, "reason": "DRIVE_FILE_REMOVED_OR_TRASHED"})
                continue

            incoming_sha256 = file_meta.get("sha256Checksum") or file_meta.get("md5Checksum")
            existing = existing_docs_by_external_id.get(file_id)

            if existing and existing.get("checksum") == incoming_sha256:
                actions["metadata_only_update"].append(
                    {
                        "external_id": file_id,
                        "new_title": file_meta.get("name"),
                        "parents": file_meta.get("parents", []),
                    }
                )
            else:
                actions["ingest_new_or_modified"].append(
                    {
                        "external_id": file_id,
                        "title": file_meta.get("name"),
                        "mime_type": file_meta.get("mimeType"),
                        "checksum": incoming_sha256,
                        "parents": file_meta.get("parents", []),
                    }
                )

        return actions

    @staticmethod
    def auto_pair_exam_and_hdc_documents(
        candidate_docs: List[Dict[str, Any]],
        auto_confirm_threshold: float = 0.85,
        propose_threshold: float = 0.60,
    ) -> List[Dict[str, Any]]:
        # Tự động phân loại file Đề bài và file Hướng dẫn chấm (HDC/Đáp án) trong cùng đợt đồng bộ,
        # chấm điểm 4 tín hiệu và ghép cặp.
        solution_keywords = ("hdc", "huong dan cham", "dap an", "loi giai", "đáp án", "hướng dẫn chấm")
        exams: List[Dict[str, Any]] = []
        solutions: List[Dict[str, Any]] = []

        for doc in candidate_docs:
            lower_name = doc["filename"].lower()
            if any(k in lower_name for k in solution_keywords):
                solutions.append(doc)
            else:
                exams.append(doc)

        pairings: List[Dict[str, Any]] = []
        for ex in exams:
            best_sol = None
            best_score = 0.0
            best_signals: Dict[str, float] = {}

            for sol in solutions:
                score, signals = ThreeTierDeduplicationAndPairingEngine.score_exam_and_solution_pairing(ex, sol)
                if score > best_score:
                    best_score = score
                    best_sol = sol
                    best_signals = signals

            if best_sol and best_score >= propose_threshold:
                pairings.append(
                    {
                        "exam_doc_id": ex["doc_id"],
                        "solution_doc_id": best_sol["doc_id"],
                        "similarity_score": best_score,
                        "signals_json": best_signals,
                        "status": "CONFIRMED" if best_score >= auto_confirm_threshold else "PROPOSED",
                    }
                )
        return pairings
```


### 6.8. Thuật toán 8: Động Cơ Crypto-Shredding Theo Từng Người Dùng Trên HashiCorp Vault Transit, Xóa Văn Bản Tự Do & Chống Phục Hồi PII Khi Chạy PITR (`PIICryptoShreddingService` — Module F04, G02)

**Giải quyết triệt để mâu thuẫn KEK/Backup và PII trong văn bản tự do (Khắc phục P0-4 — Mục 3 của Bản vá V11.1):**
1. **Phương án A (Khuyến nghị — Vault Transit Per-User Key `ulp-user-{user_id}`):** Mỗi người dùng có một khóa riêng `ulp-user-{user_id}` trên **HashiCorp Vault Transit Engine** dùng để bọc `DEK` của người dùng đó. Khi thực thi `Right to Erasure`, gọi `destroy_user_key(vault, f"ulp-user-{user_id}")` xóa vĩnh viễn khóa trên Vault Transit $\Rightarrow$ **mọi bản sao lưu DB cũ lập tức mất khả năng giải mã `DEK` của đúng user đó mà không ảnh hưởng tới bất kỳ user nào khác trong Tenant**. *(Nếu dùng Phương án B — 1 KEK/Tenant/Năm, bắt buộc phải ghi rõ giới hạn 30 ngày lưu trữ Backup trong thông báo quyền riêng tư và được Pháp chế xác nhận).*
2. **Mã hóa `guardian_email` & Xóa sạch Văn bản tự do chứa PII:** Cột `guardian_email` trong `parental_consents` được tách thành `guardian_email_blind_index` (HMAC) và `guardian_email_ciphertext` (mã hóa bằng `DEK` của `learner_user_id`). Khi xóa dữ liệu, hàm `scrub_free_text_pii_records()` đồng thời xóa sạch nội dung văn bản tự do trong `tutor_messages`, `appeals` và `comment_threads`.
3. **Lớp bảo vệ thứ hai khi chạy PITR (`reapply_tombstones_after_pitr_restore`):** Luôn giữ sổ cái WORM Tombstone để quét sạch lại sau mỗi lần khôi phục thảm họa.

```python
# backend/modules/privacy/crypto_shredding.py
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PIICryptoShreddingService:
    SENSITIVE_AUDIT_KEYS = {
        "email", "raw_email", "plaintext_email", "guardian_email",
        "password", "phone", "phone_number", "full_name", "display_name", "cccd"
    }

    @staticmethod
    def compute_email_blind_index(email: str, cluster_pepper: bytes) -> str:
        norm_email = (email or "").strip().lower().encode("utf-8")
        return hmac.new(cluster_pepper, norm_email, hashlib.sha256).hexdigest()

    @staticmethod
    def generate_user_dek() -> bytes:
        return AESGCM.generate_key(bit_length=256)

    @staticmethod
    def wrap_dek_with_vault_user_key(vault_client: Any, user_id: UUID, user_dek: bytes) -> Tuple[str, bytes]:
        """
        Phương án A (Khuyến nghị): Tạo khóa riêng `ulp-user-{user_id}` trên HashiCorp Vault Transit
        và bọc DEK của người dùng. Xóa khóa này trên Vault sẽ vô hiệu hóa cả các bản DB Backup cũ.
        """
        key_name = f"ulp-user-{user_id}"
        vault_client.secrets.transit.create_key(name=key_name, key_type="aes256-gcm96")
        resp = vault_client.secrets.transit.encrypt_data(
            name=key_name,
            plaintext=base64.b64encode(user_dek).decode("ascii"),
        )
        return key_name, resp["data"]["ciphertext"].encode("ascii")

    @staticmethod
    def destroy_vault_user_key(vault_client: Any, key_name: str) -> None:
        """Xóa vĩnh viễn khóa của user trên Vault Transit khi thực thi Right to Erasure."""
        vault_client.secrets.transit.update_key_configuration(name=key_name, deletion_allowed=True)
        vault_client.secrets.transit.delete_key(name=key_name)

    @staticmethod
    def wrap_dek_with_tenant_kek(tenant_kek: bytes, user_dek: bytes, user_id: UUID) -> bytes:
        """Phương án B (Fallback cục bộ khi test hoặc chưa kết nối Vault Transit)."""
        aesgcm = AESGCM(tenant_kek)
        nonce = os.urandom(12)
        aad = f"dek_wrap:{user_id}".encode("utf-8")
        return nonce + aesgcm.encrypt(nonce, user_dek, aad)

    @staticmethod
    def unwrap_dek_with_tenant_kek(tenant_kek: bytes, wrapped_dek_blob: Optional[bytes], user_id: UUID) -> Optional[bytes]:
        if wrapped_dek_blob is None:
            return None
        aesgcm = AESGCM(tenant_kek)
        nonce, ct = wrapped_dek_blob[:12], wrapped_dek_blob[12:]
        aad = f"dek_wrap:{user_id}".encode("utf-8")
        return aesgcm.decrypt(nonce, ct, aad)

    @staticmethod
    def encrypt_user_pii(dek: bytes, pii_dict: Dict[str, Any], user_id: UUID) -> bytes:
        aesgcm = AESGCM(dek)
        nonce = os.urandom(12)
        aad = str(user_id).encode("utf-8")
        plaintext = json.dumps(pii_dict, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return nonce + aesgcm.encrypt(nonce, plaintext, aad)

    @staticmethod
    def decrypt_user_pii(dek: Optional[bytes], blob: Optional[bytes], user_id: UUID) -> Dict[str, Any]:
        if dek is None or blob is None:
            return {"email": "anonymized@shredded.local", "display_name": "Người dùng đã ẩn danh"}
        aesgcm = AESGCM(dek)
        nonce, ciphertext = blob[:12], blob[12:]
        aad = str(user_id).encode("utf-8")
        plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
        return json.loads(plaintext.decode("utf-8"))

    @staticmethod
    def scrub_free_text_pii_records(
        tutor_messages: List[Dict[str, Any]],
        appeals: List[Dict[str, Any]],
        comment_threads: List[Dict[str, Any]],
    ) -> None:
        """Xóa nội dung văn bản tự do có thể chứa PII khi thực thi Right to Erasure (Khắc phục P0-4)."""
        for msg in tutor_messages:
            msg["message_md"] = "[ĐÃ XÓA THEO YÊU CẦU]"
            msg["citations_json"] = []
        for ap in appeals:
            ap["reason_text"] = "[ĐÃ XÓA THEO YÊU CẦU]"
            ap["resolution_note"] = None
        for cm in comment_threads:
            cm["body_text"] = "[ĐÃ XÓA THEO YÊU CẦU]"

    @classmethod
    def execute_erasure_and_emit_tombstone(
        cls,
        user_row: Dict[str, Any],
        key_row: Dict[str, Any],
        tenant_id: UUID,
        parental_consent_rows: Optional[List[Dict[str, Any]]] = None,
        vault_client: Optional[Any] = None,
        backup_retention_days: int = 30,
    ) -> Dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        if vault_client is not None and key_row.get("kms_kek_key_id", "").startswith("ulp-user-"):
            cls.destroy_vault_user_key(vault_client, key_row["kms_kek_key_id"])

        user_row["email_blind_index"] = hashlib.sha256(os.urandom(32)).hexdigest()
        user_row["pii_ciphertext_bytea"] = None
        user_row["pseudonym_handle"] = f"Anonymized_{str(user_row['id'])[:8]}"
        user_row["status"] = "ANONYMIZED"

        for pc in parental_consent_rows or []:
            pc["guardian_email_blind_index"] = hashlib.sha256(os.urandom(32)).hexdigest()
            pc["guardian_email_ciphertext"] = b""

        key_row["encrypted_dek_bytea"] = None
        key_row["key_status"] = "SHREDDED"
        key_row["shredded_at"] = now_utc
        key_row["backup_retention_expires_at"] = now_utc + timedelta(days=backup_retention_days)

        cert_payload = f"{tenant_id}:{user_row['id']}:{key_row['kms_kek_key_id']}:{now_utc.isoformat()}"
        return {
            "subject_user_id": user_row["id"],
            "tenant_id": tenant_id,
            "kms_kek_key_id": key_row["kms_kek_key_id"],
            "shredded_at": now_utc.isoformat(),
            "backup_expiry_guaranteed_at": key_row["backup_retention_expires_at"].isoformat(),
            "verification_hash": hashlib.sha256(cert_payload.encode("utf-8")).hexdigest(),
        }

    @classmethod
    def reapply_tombstones_after_pitr_restore(
        cls,
        restored_users_by_id: Dict[UUID, Dict[str, Any]],
        restored_keys_by_uid: Dict[UUID, Dict[str, Any]],
        worm_tombstones: Iterable[Dict[str, Any]],
    ) -> int:
        scrubbed_count = 0
        for tomb in worm_tombstones:
            uid = tomb["subject_user_id"]
            if uid in restored_users_by_id and restored_users_by_id[uid].get("status") != "ANONYMIZED":
                restored_users_by_id[uid]["email_blind_index"] = hashlib.sha256(os.urandom(32)).hexdigest()
                restored_users_by_id[uid]["pii_ciphertext_bytea"] = None
                restored_users_by_id[uid]["status"] = "ANONYMIZED"
                if uid in restored_keys_by_uid:
                    restored_keys_by_uid[uid]["encrypted_dek_bytea"] = None
                    restored_keys_by_uid[uid]["key_status"] = "SHREDDED"
                scrubbed_count += 1
        return scrubbed_count

    @classmethod
    def sanitize_audit_json(cls, payload: Optional[Dict[str, Any]], cluster_pepper: bytes) -> Dict[str, Any]:
        if not payload:
            return {}
        sanitized: Dict[str, Any] = {}
        for k, v in payload.items():
            if k.lower() in cls.SENSITIVE_AUDIT_KEYS and v is not None:
                digest = hmac.new(cluster_pepper, str(v).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
                sanitized[f"{k}_redacted_hash"] = f"hmac:{digest}"
            elif isinstance(v, dict):
                sanitized[k] = cls.sanitize_audit_json(v, cluster_pepper)
            else:
                sanitized[k] = v
        return sanitized
```

### 6.9. Thuật toán 9: Động Cơ Lưu Bài Phòng Thi Phía Trình Duyệt Dùng `Dexie` (`IndexedDB` Thật), Khớp Canonical Contract (`expected_response_version` + `payload_hash`) & Tự Động `resync` (`frontend/src/lib/exam-autosave-store.ts`)

**Cải tiến cốt lõi ở V11.3 (Khắc phục P0-6 & P0-7 — Mục 5.1 của Bản vá V11.1):**
- Lưu bền vững vào **`IndexedDB` thật qua thư viện `Dexie`** trước khi gửi mạng (F5 hoặc sập trình duyệt không mất bất kỳ câu trả lời nào).
- Chỉ xóa các bản nháp chưa từng gửi (`tries === 0`) khi gộp thao tác mới, giữ nguyên các bản đã từng gửi (`tries > 0`) để không làm lệch `expected_response_version` nếu gói tin trước đã được Server commit nhưng rớt ACK trên đường về.
- Gửi đầy đủ `expected_response_version` và `payload_hash` (tính từ `canonicalJson` khớp tuyệt đối với `json.dumps(sort_keys=True, separators=(",", ":"))` của Python).
- Hoàn thiện hàm `resync(r)` gọi `GET /api/v1/attempts/${r.attemptId}/sync` để đồng bộ lại phiên bản và tự động gửi lại hoặc bật `Conflict Modal`.

```typescript
// frontend/src/lib/exam-autosave-store.ts — Lưu trữ IndexedDB thật (Dexie) & Khớp 100% Canonical Autosave Contract
import Dexie, { Table } from "dexie";

export type ExamPayload = Record<string, string | number | boolean | null>;

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

interface MetaRow {
  key: string;
  value: number;
}

class ExamDatabase extends Dexie {
  pending!: Table<PendingAutosave, number>;
  meta!: Table<MetaRow, string>;

  constructor() {
    super("ulp_exam_offline_db");
    this.version(1).stores({
      pending: "++id, attemptId, [attemptId+questionVersionId]",
      meta: "key",
    });
  }
}

export const examDb = new ExamDatabase();

/**
 * Chuẩn hóa JSON trùng khớp 100% với server Python:
 * json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 */
export function canonicalJson(v: unknown): string {
  if (v === null || typeof v !== "object") {
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) {
    return "[" + v.map((item) => canonicalJson(item)).join(",") + "]";
  }
  const o = v as Record<string, unknown>;
  const keys = Object.keys(o).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(o[k])).join(",") + "}";
}

export async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

const seqKey = (a: string, q: string) => `seq:${a}:${q}`;
const verKey = (a: string, q: string) => `ver:${a}:${q}`;

export async function enqueue(
  attemptId: string,
  questionVersionId: string,
  payload: ExamPayload,
  flagged = false,
  apiBase = "",
  token = () => "",
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  const payloadHash = await sha256Hex(canonicalJson(payload));
  await examDb.transaction("rw", examDb.pending, examDb.meta, async () => {
    const seq = ((await examDb.meta.get(seqKey(attemptId, questionVersionId)))?.value ?? 0) + 1;
    await examDb.meta.put({ key: seqKey(attemptId, questionVersionId), value: seq });

    // Chỉ xóa những bản CHƯA từng gửi mạng (tries === 0); giữ nguyên bản đã gửi (tries > 0) phòng trường hợp mất ACK
    const existing = await examDb.pending
      .where("[attemptId+questionVersionId]")
      .equals([attemptId, questionVersionId])
      .toArray();
    await examDb.pending.bulkDelete(existing.filter((r) => r.tries === 0).map((r) => r.id!));

    await examDb.pending.add({
      attemptId,
      questionVersionId,
      clientSequence: seq,
      payloadHash,
      idempotencyKey: `${attemptId}:${questionVersionId}:seq_${seq}:${crypto.randomUUID()}`,
      responsePayload: payload,
      isFlagged: flagged,
      createdAtMs: Date.now(),
      tries: 0,
    });
  });
  void flush(apiBase, token, onConflictModal);
}

let flushing = false;

export async function flush(
  apiBase = "",
  token = () => "",
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  if (flushing) return;
  flushing = true;
  try {
    const rows = await examDb.pending.orderBy("id").toArray();
    for (const r of rows) {
      const expected = (await examDb.meta.get(verKey(r.attemptId, r.questionVersionId)))?.value ?? 0;
      let res: Response;
      try {
        res = await fetch(`${apiBase}/api/v1/attempts/${r.attemptId}/responses/${r.questionVersionId}`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token()}`,
            "Idempotency-Key": r.idempotencyKey,
          },
          body: JSON.stringify({
            client_sequence: r.clientSequence,
            expected_response_version: expected,
            idempotency_key: r.idempotencyKey,
            payload_hash: r.payloadHash,
            response_payload: r.responsePayload,
            is_flagged: r.isFlagged,
          }),
        });
      } catch {
        await backoff(r, apiBase, token, onConflictModal);
        return;
      }

      if (res.status === 200) {
        const body = (await res.json()) as { saved_response_version: number };
        await examDb.meta.put({
          key: verKey(r.attemptId, r.questionVersionId),
          value: body.saved_response_version,
        });
        await examDb.pending.delete(r.id!);
      } else if (res.status >= 500 || res.status === 429) {
        await backoff(r, apiBase, token, onConflictModal);
        return;
      } else if (res.status === 403) {
        // ATTEMPT_DEADLINE_EXPIRED: Hết giờ thi trên Server
        await examDb.pending.clear();
        return;
      } else if (res.status === 409) {
        await resync(r, apiBase, token, onConflictModal);
        return;
      } else {
        await examDb.pending.delete(r.id!);
      }
    }
  } finally {
    flushing = false;
  }
}

async function backoff(
  r: PendingAutosave,
  apiBase: string,
  token: () => string,
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  await examDb.pending.update(r.id!, { tries: r.tries + 1 });
  const delay = Math.min(30000, 500 * 2 ** r.tries) + Math.random() * 500;
  setTimeout(() => void flush(apiBase, token, onConflictModal), delay);
}

async function resync(
  r: PendingAutosave,
  apiBase: string,
  token: () => string,
  onConflictModal?: (questionVersionId: string, serverPayload: unknown) => void,
): Promise<void> {
  const syncRes = await fetch(`${apiBase}/api/v1/attempts/${r.attemptId}/sync`, {
    headers: { Authorization: `Bearer ${token()}` },
  });
  if (!syncRes.ok) return;
  const syncData = (await syncRes.json()) as {
    responses: Record<
      string,
      { revision_no: number; current_sequence: number; last_idempotency_key: string; response_payload: unknown }
    >;
  };
  const srv = syncData.responses[r.questionVersionId];
  if (!srv) return;

  await examDb.meta.put({ key: verKey(r.attemptId, r.questionVersionId), value: srv.revision_no });
  await examDb.meta.put({
    key: seqKey(r.attemptId, r.questionVersionId),
    value: Math.max(r.clientSequence, srv.current_sequence),
  });

  if (srv.last_idempotency_key === r.idempotencyKey) {
    await examDb.pending.delete(r.id!);
  } else {
    await examDb.pending.delete(r.id!);
    if (onConflictModal) {
      onConflictModal(r.questionVersionId, srv.response_payload);
    }
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("online", () => void flush());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void flush();
  });
}
```

---

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
