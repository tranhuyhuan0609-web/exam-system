# CLAUDE.md — Quy Chuẩn Kiến Trúc & Kỹ Thuật Hệ Thống ULP (V11.3)

Dự án: **Universal Learning & Assessment Platform (exam-system Upgrade)**  
Phiên bản kiến trúc: **V11.3 (Engineering-Hardening Unified Master)**  
Nguồn chân lý duy nhất (Single Source of Truth): `@docs/architecture/platform_manifest.yaml`

---

## 1. Sáu Quy Tắc Bất Biến (Architectural Guardrails) — BẮT BUỘC TUÂN THỦ
Khi viết code, tạo migration hoặc thiết kế API, tuyệt đối không vi phạm 6 nguyên tắc sau:
1. **Refactor & Extend**: Nâng cấp trên nền tảng hiện có (`FastAPI`, `SQLAlchemy 2.0`, `Alembic`), không đập đi xây lại từ đầu.
2. **Tách biệt 4 Cấu trúc Tri thức**: Giữ độc lập UUID định danh bất biến và phiên bản cho: `Taxonomy` (`ltree`), `Knowledge Graph` (DAG), `Curriculum` (4 cấp SGK), và `Assessment Framework` (THPTQG 2025+).
3. **5 Trục Trạng thái (Fail-Closed)**: Mọi thực thể học liệu/câu hỏi phải quản lý độc lập: `acquisition_status`, `verification_status`, `rights_status` (mặc định `PENDING_REVIEW`), `publication_status`, và `job_status`. Chỉ được `PUBLISHED` khi `VERIFIED` và `rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')`.
4. **Dữ liệu Bất biến (DB-Enforced Immutability)**: Cấm `UPDATE/DELETE` trên dữ liệu gốc tầng Bronze (`RAW_SOURCE`), các bản `content_versions` / `question_versions` / `assessment_versions` đã Publish, `response_revisions` và `audit_events`. Mọi chỉnh sửa phải tạo `version_no + 1`.
5. **PostgreSQL là Nguồn Sự Thật Duy Nhất (RPO = 0)**: Redis chỉ làm cache đề công khai (đã lược bỏ đáp án) và broker. Autosave phòng thi bắt buộc `COMMIT` xuống PostgreSQL (Synchronous Standby) trước khi trả HTTP 200 ACK.
6. **Zero Shared Table Ownership**: Mỗi bảng PostgreSQL chỉ thuộc quyền sở hữu ghi (`INSERT/UPDATE/DELETE`) của **duy nhất 1 Module** (A01–G06). Giao tiếp liên module bắt buộc qua Application Service công khai hoặc Transactional Outbox (`outbox_events` + `inbox_events`).

---

## 2. Quy Chuẩn Bảo Mật, Database & Thuật Toán Cốt Lõi
- **Multi-Tenant RLS**: Mọi bảng nghiệp vụ (kể cả bảng con) đều có `tenant_id` và bật `FORCE ROW LEVEL SECURITY` dùng `(SELECT current_app_tenant_id())`. Cấm cấp `BYPASSRLS` cho mọi runtime/worker role.
- **Zero-Answer-Leak**: Tuyệt đối tách biệt đáp án sang `question_version_answers` và `assessment_answer_keys`. Role phòng thi (`ulp_exam_session_rw`) và nạp đề (`ulp_worker_ingestion`) bị `REVOKE SELECT` trên 2 bảng này.
- **SymPy Sandbox**: Cấm gọi `sp.sympify()` trực tiếp trên chuỗi do LLM sinh ra. Bắt buộc chạy qua tiến trình cô lập `python -I sympy_sandbox_worker.py` với whitelist hàm và giới hạn RAM/CPU.
- **Thư viện PDF**: Sử dụng `pypdfium2` + `pdfplumber` (loại bỏ hoàn toàn `PyMuPDF` do dính giấy phép AGPLv3).
- **Autosave Phòng Thi**: Frontend dùng `Dexie` (IndexedDB thật) gửi kèm `Idempotency-Key`, `client_sequence`, `expected_response_version` và `payload_hash` (SHA-256 canonical JSON).

---

## 3. Bản Đồ Tài Liệu Tham Chiếu Chi Tiết (Đọc Khi Cần Thiết)
Trước khi triển khai một tác vụ cụ thể, hãy đọc tài liệu tương ứng dưới đây:
- **Cấu hình chuẩn 47 Modules, 11 Gates (`GATE-01..10`), Ngưỡng IRT & Lịch Sprint**:  
  `@docs/architecture/platform_manifest.yaml`
- **Đặc tả 47 Modules (A01–G06), 17 ADRs, Ma trận sở hữu bảng & 6 Web Workspaces**:  
  `@docs/architecture/handover-v11.3/01-guardrails-and-modules.md`
- **Schema SQL đầy đủ, Composite FK, RLS Policies, Triggers & Phân quyền 7 DB Roles**:  
  `@docs/architecture/handover-v11.3/02-database-ddl-and-rls.md`
- **Hợp đồng Pydantic v2, Event Bus Catalog & 9 Thuật toán lõi (OCR/Vector, Dedup Hóa học, SymPy, Autosave, FSRS-5, Anti-SSRF Crawler, Drive Zip-Bomb, Vault Crypto-Shredding, Dexie Store)**:  
  `@docs/architecture/handover-v11.3/03-contracts-and-algorithms.md`
- **Quy trình Git/CI 6 cổng, Zero-Downtime Migration, Golden Corpus & 14 Runbooks sự cố**:  
  `@docs/architecture/handover-v11.3/04-cicd-gates-and-runbooks.md`

---

## 4. Lệnh Kiểm Tra Bắt Buộc (CI Gates)
Trước khi kết thúc bất kỳ thay đổi code nào, hãy đảm bảo vượt qua:
- Kiểm tra biên giới import giữa các module: `lint-imports` (tuân thủ `.importlinter`)
- Kiểm tra mã Gate thống nhất (cấm dùng mã `RG-xx` cũ): `bash ci/check_gate_ids.sh`
- Chạy test đơn vị & hợp đồng: `pytest`
