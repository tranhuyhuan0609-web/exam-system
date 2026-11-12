# CLAUDE.md — Chỉ Dẫn Kiến Trúc & Quy Tắc Thực Thi Cho Claude Code (ULP V11.3)

Tệp này được Claude Code tự động nạp khi khởi động trong thư mục gốc của repository `exam-system`. Mọi thay đổi mã nguồn, migration, test và cấu hình phải tuân thủ nghiêm ngặt bản đặc tả **`docs/architecture/TL_Handover_V11_3_Engineering_Hardening_Unified.md`** và nguồn chuẩn **`docs/architecture/platform_manifest.yaml` (v11.3.0)**.

## 1. Quy Tắc Bất Biến Bắt Buộc (Release-Blocking Guardrails `H-01` .. `H-07`)

1. **Không đập đi xây lại từ đầu (Refactor & Extend):** Kế thừa và nâng cấp từ cấu trúc hiện hữu của repo `exam-system` theo bảng ánh xạ ở **Chương 2** và quy trình Zero-Downtime Migration 5 bước (**Mục 7.4**).
2. **`H-01` — Cô lập Đa thuê bao (RLS 100% Bảng) & Kiểm soát Học liệu Công khai:**
   - Mọi bảng nghiệp vụ phải bật `ENABLE ROW LEVEL SECURITY` và `FORCE ROW LEVEL SECURITY`.
   - Dùng biểu thức `(SELECT current_app_tenant_id())` (InitPlan) trong mọi policy RLS để tối ưu hiệu năng ở tải 5.000 CCU.
   - Bảng con trong nhóm `hybrid_public_tables` bắt buộc kiểm tra trạng thái `publication_status = 'PUBLISHED' AND is_immutable IS TRUE` của bảng cha qua `EXISTS` (trên `knowledge_edges` phải kiểm tra cả `source_node_id` và `target_node_id` đang `is_active IS TRUE`).
3. **`H-02` — Cô lập Tiến trình `SymPy` (Fail-Closed 100%):**
   - Tuyệt đối **cấm gọi `sp.sympify()` trực tiếp trong tiến trình API/Worker** và **cấm mọi nhánh fallback chạy in-process** khi thiếu file worker.
   - Chỉ gọi qua tiến trình cô lập `python -I sympy_sandbox_worker.py` (kiểm tra `ALLOWED_SYMPY_NAMES`, chặn `__`, giới hạn `RLIMIT_AS = 256MB`, `RLIMIT_CPU = 5s`, tắt `__builtins__ = {}`); nếu thiếu worker thì trả ngay `SANDBOX_WORKER_UNAVAILABLE`.
4. **`H-03` — Phân quyền 7 DB Roles theo Nguyên tắc Tối thiểu (Least-Privilege):**
   - Tất cả 7 roles (`ulp_migrator`, `ulp_app_rw`, `ulp_exam_session_rw`, `ulp_worker_ingestion`, `ulp_worker_grading`, `ulp_system_scheduler`, `ulp_analytics_ro`) đều **`NOBYPASSRLS`**.
   - `ulp_exam_session_rw` và `ulp_worker_ingestion` bị `REVOKE ALL` trên `question_version_answers` và `assessment_answer_keys`.
   - `ulp_worker_grading` có policy riêng (`grading_read_question_answers`, `grading_read_answer_keys`) để đọc đáp án của cả đề hệ thống `SYSTEM_PUBLIC`.
   - Các job nền xuyên Tenant (`outbox_relay`, `attempt_expiry`, `metric_rollup`) chạy dưới `ulp_system_scheduler` với biến phiên `SET LOCAL app.system_job = '<job_name>'`.
5. **`H-04` — Hợp đồng Autosave Tất định & Lưu trữ Ngoại tuyến `Dexie` (`IndexedDB` thật):**
   - Trường `expected_response_version: int` là **bắt buộc (không `Optional`)** ở cả Pydantic Contract lẫn hàm xử lý backend.
   - Kiểm tra `Idempotency-Key` trong `autosave_idempotency_registry` trước tiên, đối chiếu `payload_hash` (nếu lệch trả `422 PAYLOAD_HASH_MISMATCH`), kiểm tra `attempt.status == 'IN_PROGRESS'` (chống đua lệnh với `Submit`) và `deadline_at`.
   - Phía Frontend (`frontend/src/lib/exam-autosave-store.ts`) bắt buộc lưu vào `IndexedDB` thật qua thư viện `Dexie` trước khi gửi mạng.
6. **Mã Cổng Nghiệm thu Duy nhất (`GATE-01` .. `GATE-10`):**
   - Chỉ sử dụng mã `GATE-01..GATE-05`, `GATE-06a` (Toán/Lý), `GATE-06b` (Hóa/Sinh), `GATE-07..GATE-10` theo đúng `docs/architecture/platform_manifest.yaml` (kiểm tra bằng `ci/check_gate_ids.sh`).

## 2. Cấu Trúc Thư Mục Tài Liệu Đã Tách Nhỏ Để Claude Code Đọc Nhanh (Không Tràn Token)

Thay vì đọc toàn bộ file lớn 2.500 dòng cùng lúc, hãy đọc từng file chuyên đề trong `docs/architecture/` hoặc đọc theo số dòng dưới đây:
- `docs/architecture/platform_manifest.yaml`: Nguồn chuẩn duy nhất về 47 modules, 11 Gates, 7 DB Roles, 4 bậc IRT.
- `docs/architecture/chapters/00_mo_dau_va_manifest.md`: Quy tắc quản trị & Tóm tắt các bản vá P0/P1.
- `docs/architecture/chapters/01_kien_truc_va_17_adrs.md`: Chương 1 (17 ADRs, DPIA, Anti-Cheat, Chi phí LLM/GPU) & Chương 2 (Ánh xạ từ repo `exam-system`).
- `docs/architecture/chapters/03_dac_ta_47_modules_va_6_workspaces.md`: Chương 3 (Đặc tả 47 Modules `A01–G06` & 6 Giao diện Web Workspaces).
- `docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql`: Toàn bộ Physical DDL, RLS, Triggers & Grants tách sẵn thành file `.sql`.
- `docs/architecture/chapters/05_event_bus_va_pydantic_contracts.md`: Chương 5 (Event Catalog, Pydantic v2 Contracts & Workflows 8 bước).
- `docs/architecture/chapters/06_9_thuat_toan_loi.md`: Chương 6 (8 thuật toán Python + 1 thuật toán TypeScript `Dexie`).
- `docs/architecture/chapters/07_08_ci_cd_lo_trinh_waves_gates.md`: Chương 7 & Chương 8 (Git/CI 6 cổng, Lộ trình Sprint 1–14, `Wave 0..2`, `GATE-01..10`).
- `docs/architecture/chapters/09_10_qa_k6_rls_tests_14_runbooks.md`: Chương 9 & Chương 10 (11 đề Golden Corpus, SLOs, `k6`, `verify_rls_hardening.sql` & 14 Runbooks).

## 3. Bảng Tra Cứu Dòng Trong File Tổng (`docs/architecture/TL_Handover_V11_3_Engineering_Hardening_Unified.md`)

| Vị trí Dòng | Nội dung Chương / Mục trong File Tổng |
| :--- | :--- |
| Dòng `9` | `PHẦN MỞ ĐẦU: QUY TẮC QUẢN TRỊ KỸ THUẬT, CANONICAL MANIFEST & TỔNG HỢP HỢP NHẤT BẢN VÁ V11.1` |
| Dòng `83` | `CHƯƠNG 1. CHỈ ĐẠO KỸ THUẬT TỪ TECH LEAD & 6 QUY TẮC BẤT BIẾN (ARCHITECTURAL GUARDRAILS)` |
| Dòng `184` | `CHƯƠNG 2. BẢNG ÁNH XẠ NÂNG CẤP TỪ REPO `EXAM-SYSTEM` & MA TRẬN SỞ HỮU BẢNG ĐỘC QUYỀN` |
| Dòng `219` | `CHƯƠNG 3. ĐẶC TẢ CHI TIẾT ĐẦY ĐỦ 47 MODULES ĐỘC LẬP (A01 – G06, KHÔNG GỘP DÒNG)` |
| Dòng `337` | `CHƯƠNG 4. PHYSICAL POSTGRESQL DDL TOÀN DIỆN (FULL SCHEMA, COMPOSITE FK TENANCY, RLS & IMMUTABILITY TRIGGERS)` |
| Dòng `2643` | `CHƯƠNG 5. DANH MỤC SỰ KIỆN NỘI BỘ (EVENT BUS CATALOG) & HỢP ĐỒNG PYDANTIC V2` |
| Dòng `2880` | `CHƯƠNG 6. CHÍN THUẬT TOÁN LÕI HOÀN CHỈNH (ĐÃ CHẠY KIỂM CHỨNG THỰC TẾ TRÊN PYTHON 3 — 0 LỖI REGEX/IMPORT/TYPE)` |
| Dòng `2882` | `6.1. Thuật toán 1: Xử lý Tài liệu Đề thi Việt Nam Có Tách Bảng Khỏi Hình Vector (`VietnameseExamDocumentProcessor` — Module A04, A05, A06)` |
| Dòng `3202` | `6.2. Thuật toán 2: Khử Trùng 3 Tầng Bằng Forward Chemical Tokenizer (Không Dùng Variable-Width Lookbehind) & Ghép Cặp 4 Tín Hiệu (`ThreeTierDeduplicationAndPairingEngine` — Module A08)` |
| Dòng `3308` | `6.3. Thuật toán 3: Kiểm Chứng Đáp Án Độc Lập Bằng Tiến Trình `SymPy` Cô Lập (`sympy_sandbox_worker.py` & `sympy_runner.py` — Module C02, E04)` |
| Dòng `3461` | `6.4. Thuật toán 4: Bốc Đề Tất Định, Giao Thức Idempotency-First Autosave (Kèm Kiểm Tra `deadline_at` & Payload Hash) & Chấm Điểm Chuẩn THPTQG 2025+ (`DeterministicExamAndAutosaveEngine` — Module C04, C05, C06, C07)` |
| Dòng `3699` | `6.5. Thuật toán 5: Lịch Ôn Tập Chuẩn FSRS v5 (Đủ 19 Trọng Số `W[0..18]`) & Cổng Trưởng Thành IRT Độc Lập (`AdaptiveMasteryFSRSAndIRTEngine` — Module D02, D04, D06)` |
| Dòng `3910` | `6.6. Thuật toán 6: Bộ Cào Câu Hỏi Hợp Pháp Dùng `ip_obj.is_global` + Kiểm Tra Mỗi Bước Redirect (`PermittedWebCrawlerEngine` — Module A02)` |
| Dòng `4073` | `6.7. Thuật toán 7: Đồng Bộ Google Drive 2 Chế Độ (`Picker + drive.file` & `Shared Drive Service Account`), Bung Nén Chống Zip Bomb & Ghép Cặp (`GoogleDriveDifferentialSyncEngine` — Module A03)` |
| Dòng `4276` | `6.8. Thuật toán 8: Động Cơ Crypto-Shredding Theo Từng Người Dùng Trên HashiCorp Vault Transit, Xóa Văn Bản Tự Do & Chống Phục Hồi PII Khi Chạy PITR (`PIICryptoShreddingService` — Module F04, G02)` |
| Dòng `4457` | `6.9. Thuật toán 9: Động Cơ Lưu Bài Phòng Thi Phía Trình Duyệt Dùng `Dexie` (`IndexedDB` Thật), Khớp Canonical Contract (`expected_response_version` + `payload_hash`) & Tự Động `resync` (`frontend/src/lib/exam-autosave-store.ts`)` |
| Dòng `4686` | `CHƯƠNG 7. QUY TRÌNH PHÒNG DEV, TRUNK-BASED GIT FLOW, CI/CD 6 CỔNG, DoR/DoD & ZERO-DOWNTIME MIGRATION` |
| Dòng `4735` | `CHƯƠNG 8. LỘ TRÌNH 3 RELEASE TRACKS (`M0 + SPRINT 1–14 = 30 TUẦN` HOẶC `PHƯƠNG ÁN B = 26 TUẦN`), KẾ HOẠCH NGUỒN LỰC & 11 RELEASE GATES (`GATE-xx`)` |
| Dòng `4743` | `8.1. Bảng Phân Công Chi Tiết `M0` và `Sprint 1 – Sprint 14` (Đội ngũ 10 Kỹ sư)` |
| Dòng `4762` | `8.2. Hai Phương Án Ngân Sách Công Sức & Thời Lượng (`Phương án A: 30 Tuần` vs `Phương án B: 26 Tuần`)` |
| Dòng `4770` | `8.3. Mười Một Cổng Nghiệm Thu Phát Hành Chuẩn Hóa (`GATE-01` đến `GATE-10` — Kiểm Tra Tự Động Bằng `ci/check_gate_ids.sh`)` |
| Dòng `4802` | `8.4. Thứ Tự Ưu Tiên Triển Khai `Wave 0 -> Wave 1 -> Wave 2` & Ánh Xạ `G0..G6` Với `GATE-01..10` (Hợp Nhất Phụ Lục Hardening V11.3)` |
| Dòng `4814` | `CHƯƠNG 9. MA TRẬN KIỂM THỬ HỒI QUY 11+ ĐỀ MẪU (GOLDEN CORPUS + HELD-OUT) & MỤC TIÊU SLOs` |
| Dòng `5067` | `CHƯƠNG 10. SỔ TAY VẬN HÀNH & XỬ LÝ SỰ CỐ SẢN XUẤT (14 OPERATIONAL RUNBOOKS — ĐẦY ĐỦ OWNER, SEVERITY, ROLLBACK)` |

## 4. Thứ Tự Thực Thi Cho Claude Code (`Wave 0 -> Wave 1 -> Wave 2`)

1. **Khi bắt đầu dự án (`Wave 0 / M0`):**
   - Đối chiếu `app/models.py` hiện tại với `docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql`.
   - Tạo migration Alembic cho 7 DB Roles (`NOBYPASSRLS`), RLS policies có `(SELECT current_app_tenant_id())`, `ulp_system_scheduler`, và chạy `tests/security/verify_rls_hardening.sql`.
   - Cài đặt `SymPySandboxWorker` (`python -I`, fail-closed) và `process_autosave_idempotent_first` (`expected_response_version: int` bắt buộc) kèm unit tests.
## 5. Ba Quy Tắc Làm Việc Bắt Buộc (Principal Architect & Tech Lead)

1. **HỎI NGAY KHI CHƯA RÕ (Zero Blind Assumptions):** Nếu có bất kỳ điểm nào chưa rõ về nghiệp vụ, cấu hình môi trường thực tế, hoặc phát hiện xung đột giữa code hiện tại trong `app/` và đặc tả V11.3, bắt buộc phải dừng lại hỏi người dùng (kèm các phương án lựa chọn rõ ràng), tuyệt đối không tự ý đoán mò.
2. **CHỜ REVIEW SAU MỖI BƯỚC (Step-by-Step Review Gate):** Chia nhỏ kế hoạch và quá trình code thành từng bước độc lập. Mỗi khi hoàn thành 1 bản kế hoạch hoặc 1 bước triển khai, phải tóm tắt kết quả (file đã tạo/sửa, kết quả test) và DỪNG LẠI CHỜ NGƯỜI DÙNG REVIEW & ĐỒNG Ý rồi mới được làm bước tiếp theo.
3. **CHỦ ĐỘNG ĐỀ XUẤT CẢI TIẾN (Proactive Improvements):** Trong quá trình đối chiếu code hiện có với kiến trúc V11.3, nếu phát hiện điểm nào có thể thiết kế hoặc tối ưu tốt hơn (về hiệu năng, bảo mật, cấu trúc DB/code hay trải nghiệm), hãy lập riêng một mục "💡 Đề xuất cải thiện để bạn cân nhắc" (nêu rõ ưu/nhược điểm) để người dùng quyết định.

## 6. Kế Hoạch Triển Khai & Bố Cục Code Đã Chốt

- Kế hoạch theo từng chặng (Wave 0 `H-01..H-04` → Track 1 → Track 2 → Track 3), các quyết định đã chốt và câu hỏi còn mở: `docs/architecture/IMPLEMENTATION_PLAN_V11_3.md`.
- Bố cục code của repo này: `app/modules/<tên>/{public.py, router.py, internal/}` và `app/contracts/` (thay cho `backend/modules/...` và `backend/contracts/...` trong tài liệu); nhân dùng chung ở `app/platform/`.
- Lệnh kiểm tra trước khi kết thúc mỗi chặng: `ruff check .` · `lint-imports` · `bash ci/check_gate_ids.sh` · `alembic upgrade head && alembic check` · `pytest -q`.
