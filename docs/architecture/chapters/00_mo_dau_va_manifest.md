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

