# Kế Hoạch Thực Thi Tuần Tự Từng Task Cho Claude Code (ULP V11.3)

> **Tài liệu chỉ huy dành cho Claude Code.**
> Cập nhật ngày: `14/11/2026` (trên nền commit `4cbdbe9` của nhánh `main`).
> Nguồn chuẩn đối chiếu bắt buộc:
> - `docs/architecture/platform_manifest.yaml` (v11.3.0)
> - `docs/architecture/IMPLEMENTATION_PLAN_V11_3.md`
> - `docs/architecture/chapters/03_dac_ta_47_modules_va_6_workspaces.md`
> - `docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql`
> - `docs/architecture/chapters/05_event_bus_va_pydantic_contracts.md`
> - `docs/architecture/DDL_DRY_RUN_FINDINGS.md` & `docs/architecture/BACKFILL_MAP.md`

---

## PHẦN I. QUY TẮC KỶ LUẬT SẮT CHO CLAUDE CODE (BẮT BUỘC TUÂN THỦ 100%)

1. **CHỈ LÀM LẦN LƯỢT TỪNG TASK MỘT (`ONE TASK AT A TIME`):**
   - Tuyệt đối **không tự ý gộp nhiều task**, **không nhảy cóc**, **không tự chạy sang Task tiếp theo** khi chưa có lệnh duyệt của chủ dự án.
   - Khi hoàn thành 1 Task: chạy đủ bộ lệnh kiểm tra ở Mục 5 bên dưới, báo cáo tóm tắt (file đã tạo/sửa, migration ID, kết quả test, đề xuất cải thiện nếu có) và **DỪNG LẠI CHỜ DUYỆT (`STOP & WAIT FOR REVIEW`)**.
2. **BÁM SÁT ĐẶC TẢ — CẤM TỰ ĐẶT TÊN / TỰ BỊA THIẾT KẾ (`ZERO INVENTED DESIGN`):**
   - **Tên module:** Bắt buộc dùng đúng 47 thư mục đã có sẵn trong `app/modules/<tên>/` và `.importlinter` (tuyệt đối không tạo thư mục lệch tên như `access_control`, `question_bank`, `content_studio`, `governance`, `study_guide`, `identity`...).
   - **Tên bảng, cột, kiểu dữ liệu, CHECK constraint, RLS policy, Trigger và GRANT:** Bắt buộc lấy nguyên bản từ `docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql` và các bản vá `F-01..F-04` trong `docs/architecture/DDL_DRY_RUN_FINDINGS.md`.
   - **Đường dẫn API (`/api/v1/...`) và Pydantic schema:** Bắt buộc lấy đúng theo `docs/architecture/chapters/03_dac_ta_47_modules_va_6_workspaces.md` và `app/contracts/`.
3. **TÁI SỬ DỤNG LÕI THUẦN (`PURE CORE`) ĐÃ CÓ SẴN TRONG `app/modules/<tên>/internal/`:**
   - Hiện tại **47/47 module** đã có sẵn tầng **Pure Core** (`public.py` + `internal/*.py` + `1.577` unit tests).
   - Khi làm tầng Database (`internal/models.py`, `internal/service.py`) và tầng HTTP (`router.py`), Claude Code **bắt buộc phải gọi vào các hàm/dataclass Pure Core đã có sẵn** trong module đó, không viết lại logic nghiệp vụ trùng lặp ở router hay service.
4. **QUY TẮC MIGRATION ALEMBIC ĐƠN TUYẾN (`SINGLE ALEMBIC HEAD`):**
   - Trước khi tạo migration mới, kiểm tra `alembic heads` (đầu hiện tại là `c9d0e1f2a3b4`). Mỗi migration mới phải nối tiếp tuyến tính (`down_revision` trỏ đúng head liền trước), dùng builder trong `app/platform/rls.py` (`strict_tenant_rls`, `hybrid_read_root`, `hybrid_read_child`, `hybrid_write_rls`, `grading_read`, `system_job_policy`, `partition_lockdown`, `invoker_view`), cấp `GRANT` tường minh theo bảng khớp `tests/security/grants_manifest.csv`, và đăng ký `internal/models.py` vào `app/model_registry.py`.
5. **BỘ LỆNH KIỂM TRA BẮT BUỘC TRƯỚC KHI KẾT THÚC MỖI TASK:**
   ```bash
   ruff check .
   lint-imports
   bash ci/check_gate_ids.sh
   alembic upgrade head && alembic check
   pytest -q
   ```
6. **HỎI NGAY KHI CHƯA RÕ (`ZERO BLIND ASSUMPTIONS`):**
   - Nếu gặp điểm bất khả kháng hoặc cần quyết định nghiệp vụ (ví dụ các câu hỏi mở ở Mục 5 của `IMPLEMENTATION_PLAN_V11_3.md`), phải dừng lại hỏi chủ dự án kèm phương án lựa chọn rõ ràng.

---

## PHẦN II. HIỆN TRẠNG DỰ ÁN TRƯỚC KHI BẮT ĐẦU CHUỖI TASK

- **Đã hoàn thành 100%:**
  - Biên kiến trúc `.importlinter` (52 contracts), `app/contracts/*`, `app/platform/*`, `app/ops/*`.
  - **Pure Core (`internal/*.py` + `public.py`) của cả 47/47 module** (1.577 Python unit tests pass).
  - **Frontend Core Libraries (`frontend/src/lib/*.ts`)** bao gồm `Dexie` offline store, canonical JSON, server clock, block studio linter, proctor, submission state machine (179 Vitest tests pass).
  - **Nền DB & 3 module có bảng thật:** `M0.2 foundation` (`c1d2e3f4a5b6`), `G03 outbox` (`a7b8c9d0e1f2`, `b8c9d0e1f2a3`), `B04 taxonomy` + `B06 curriculum` (`c9d0e1f2a3b4`).
  - **PDF Engine (`ADR-010`):** Đã xong `PDFEngineAdapter` (`app/infrastructure/pdf_engine.py`) cùng **PR A** và **PR B**.
- **Còn thiếu (sẽ giải quyết dứt điểm qua 36 Task tuần tự dưới đây):**
  1. Hoàn tất **PR C của PDF Engine (`W0.5`)** và gỡ hẳn `pymupdf`/`fitz` khỏi repo (`TASK-01` .. `TASK-03`).
  2. Xây dựng **ORM Models + Alembic Migration + DB Service + `/api/v1` Router** cho **Track 1 — Core MVP (Sprint 1–6)** (`TASK-04` .. `TASK-22`).
  3. Chạy **Backfill dữ liệu thực tế** từ bảng `legacy_*` sang bảng V11.3 (`TASK-23`).
  4. Xây dựng **Next.js App Router UI cho Workspace 1–6** kết nối `/api/v1` thật (`TASK-24` .. `TASK-27`).
  5. Xây dựng **ORM Models + Alembic Migration + DB Service + `/api/v1` Router** cho **Track 2 (Sprint 7–9)** và **Track 3 (Sprint 10–14)** (`TASK-28` .. `TASK-36`).

---

## PHẦN III. DANH SÁCH 36 TASK TUẦN TỰ CHI TIẾT CHO CLAUDE CODE

---

### GIAI ĐOẠN 0: ĐÓNG KÍN WAVE 0 — LOẠI BỎ HOÀN TOÀN `PyMuPDF` (`ADR-010` PR C)

#### `TASK-01` — Chuyển đổi PDF Slicing, Merging & OCR Band khỏi `PyMuPDF` (G1 + G7)
- **Đọc trước:** `docs/architecture/PDF_ENGINE_MIGRATION.md` (dòng 103–186).
- **Phạm vi sửa:**
  - `app/infrastructure/pdf_engine.py`: bổ sung helper cắt/ghép trang bằng `pypdfium2.PdfDocument.import_pages()` và đóng dấu/chèn chữ bằng `pypdfium2` / `Pillow` hoặc `pypdf` + `reportlab` (chỉ dùng thư viện giấy phép BSD/MIT/Apache-2.0).
  - `app/application/pdf_preview.py`: thay `pymupdf` bằng `PDFEngineAdapter` để cắt 2 trang đầu (`PREVIEW_PDF`) và gắn watermark/footer.
  - `app/pipeline/harvest/anthologies.py`: thay `fitz.open` / `insert_pdf` bằng adapter.
  - `app/pipeline/use_cases/library_split.py`: thay `pymupdf.Pixmap.pdfocr_tobytes` và `insert_pdf` bằng `PDFEngineAdapter` + `pytesseract` (hoặc fallback an toàn khi thiếu binary `tesseract`).
- **Tiêu chí nghiệm thu (DoD):**
  - 0 dòng `import fitz` hoặc `import pymupdf` trong `pdf_preview.py`, `anthologies.py`, `library_split.py`.
  - `pytest tests/test_freemium_pro.py tests/test_library_publish.py tests/test_library_split.py -q` xanh 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-01`.**

#### `TASK-02` — Chuyển đổi PDF Redaction & Branded Exam Stamping khỏi `PyMuPDF` (G1 + G2 + G3)
- **Đọc trước:** `docs/architecture/PDF_ENGINE_MIGRATION.md` (mục G1, G2, G3) và `docs/architecture/chapters/03_dac_ta_47_modules_va_6_workspaces.md` (mục `A04`, `A06`).
- **Phạm vi sửa:**
  - `app/ai/figures.py`, `app/ai/essay_exam.py`, `app/ai/normalize.py`, `app/cli.py`: thay `add_redact_annot` / `apply_redactions` và `_open_pymupdf` bằng cơ chế loại bỏ page-object / che vùng trên `pypdfium2` + `Pillow` trong `PDFEngineAdapter`.
  - `app/application/exam_pdf.py` và `universal_ingestor.py`: thay `fitz` khi mở rộng đỉnh trang +70pt (nếu `Câu 1` có $y_0 < 120\text{pt}$), che header cũ và đóng dấu logo/footer `H2Edu`.
- **Tiêu chí nghiệm thu (DoD):**
  - `git grep -n "fitz\|pymupdf" -- app/ universal_ingestor.py` trả về **0 kết quả thực thi** (chỉ còn chú thích nếu có).
  - `pytest tests/test_figures.py tests/test_figure_bands.py tests/test_local_text.py -q` xanh 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-02`.**

#### `TASK-03` — Chuyển đổi Test Fixtures khỏi `PyMuPDF` & Gỡ `pymupdf` khỏi `requirements.txt` (G8 + Gate 6)
- **Đọc trước:** `docs/architecture/PDF_ENGINE_MIGRATION.md` (dòng 88–102) và `app/ops/sbom_license_audit.py`.
- **Phạm vi sửa:**
  - Chuyển toàn bộ các file test (`tests/test_ai_import.py`, `tests/test_answer_keys.py`, `tests/test_books.py`, `tests/test_exam_import_keys.py`, `tests/test_figure_bands.py`, `tests/test_figures.py`, `tests/test_freemium_pro.py`, `tests/test_library_publish.py`, `tests/test_library_split.py`) sang dùng PDF builder của `tests/test_pdf_engine.py` / `Pillow`.
  - Xóa dòng `pymupdf>=1.24` khỏi `requirements.txt`.
  - Thêm kiểm tra tĩnh trong `tests/test_architecture.py`: cấm tuyệt đối `import fitz` / `import pymupdf` trong toàn bộ `app/` và `tests/`, đồng thời chạy `audit_requirements_file(Path("requirements.txt"))` từ `app/ops/sbom_license_audit.py` đảm bảo `passed is True`.
- **Tiêu chí nghiệm thu (DoD):**
  - Toàn bộ `pytest -q` và `audit_requirements_file` xanh 100%; `pymupdf` biến mất hoàn toàn khỏi `requirements.txt`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-03`.**

---

### GIAI ĐOẠN 1: TRACK 1 — SPRINT 1 (NỀN TẢNG QUẢN TRỊ, TRUY CẬP, TÀI SẢN & TIẾP NHẬN)

#### `TASK-04` — Hoàn thiện Service & Admin Router cho `G03 outbox` (`/api/v1/admin/outbox/*`, `/api/v1/admin/dlq/*`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `G03`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `outbox_events`, `inbox_events`, `dead_letter_items`), và mã hiện có trong `app/modules/outbox/`.
- **Việc cần làm:**
  - Mở rộng `app/modules/outbox/internal/service.py` (kết nối `outbox_events`, `inbox_events` với hàm thuần `relay.py`, chạy relay dưới `system_job_transaction(session, "outbox_relay")`).
  - Tạo `app/modules/outbox/router.py` cung cấp:
    - `GET /api/v1/admin/outbox/stats`
    - `GET /api/v1/admin/dlq`
    - `POST /api/v1/admin/dlq/{id}/replay`
  - Gắn router vào `app/main.py` và viết integration test `tests/test_outbox_api.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Gửi lặp cùng `event_id` 5 lần vào Inbox consumer chỉ tạo 1 tác dụng phụ duy nhất; relay chạy dưới `ulp_system_scheduler`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-04`.**

#### `TASK-05` — DB Models, Migration, Service & Router cho `G01 config` (`platform_settings`, `feature_flags`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `G01`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `platform_settings`, `feature_flags`).
- **Việc cần làm:**
  - Tạo `app/modules/config/internal/models.py` (`PlatformSetting`, `FeatureFlag`), đăng ký vào `app/model_registry.py`.
  - Tạo migration Alembic cho `platform_settings`, `feature_flags` kèm RLS và `GRANT` đúng chuẩn `grants_manifest.csv`.
  - Tạo `app/modules/config/internal/service.py` (kết nối với `app/modules/config/internal/flags.py` đã có) và `app/modules/config/router.py`:
    - `GET /api/v1/admin/feature-flags`
    - `PATCH /api/v1/admin/feature-flags/{key}`
    - `PATCH /api/v1/admin/config/{key}`
  - Viết test DB + API `tests/test_config_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Cập nhật `feature_flag` ghi nhận thay đổi tức thì và sinh bản ghi sự kiện kiểm toán.
  - **Dừng lại chờ duyệt sau khi xong `TASK-05`.**

#### `TASK-06` — DB Models, Migration, Service & Router cho `F05 access` (`roles`, `permissions`, `role_permissions`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `F05`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `roles`, `permissions`, `role_permissions`), `IMPLEMENTATION_PLAN_V11_3.md` (mục 2 & mục 5 câu hỏi xác thực).
- **Việc cần làm:**
  - Tạo `app/modules/access/internal/models.py` (`Role`, `Permission`, `RolePermission` và bảng gán role cho membership theo Chương 4), đăng ký vào `app/model_registry.py`.
  - Tạo migration Alembic kèm RLS (`strict_tenant_rls` cho bảng có `tenant_id`) và `GRANT` chuẩn.
  - Tạo `app/modules/access/internal/service.py` (gọi `app/modules/access/internal/evaluator.py`) và dependency FastAPI đặt biến phiên `app.current_tenant_id` qua `tenant_transaction`.
  - Tạo `app/modules/access/router.py`:
    - `GET /api/v1/permissions/me`
    - `POST /api/v1/roles`
    - `PUT /api/v1/memberships/{id}/roles`
  - Viết bộ test chống IDOR chéo tenant `tests/test_access_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - 0 trường hợp User thuộc Tenant A đọc/sửa được tài nguyên của Tenant B (IDOR = 0).
  - **Dừng lại chờ duyệt sau khi xong `TASK-06`.**

#### `TASK-07` — DB Models, Migration (`users` → `legacy_users`), Service & Router cho `F04 privacy`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `F04`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `users`, `memberships`, `parental_consents`, `user_pii_encryption_keys`, `erasure_certificates`), `IMPLEMENTATION_PLAN_V11_3.md` (Quyết định #2 và #7).
- **Việc cần làm:**
  - Migration Alembic: đổi tên bảng `users` cũ thành `legacy_users` (cập nhật `__tablename__ = "legacy_users"` trong `app/models.py` để giao diện Jinja cũ tiếp tục chạy bình thường); tạo các bảng V11.3 `users`, `memberships`, `parental_consents`, `user_pii_encryption_keys`, `erasure_certificates` với RLS đúng Quyết định #7 (`user_pii_encryption_keys` thu hồi quyền trực tiếp của `ulp_app_rw`, chỉ truy cập qua hàm bảo mật của F04).
  - Tạo `app/modules/privacy/internal/models.py`, `service.py` (kết nối `consent.py` và `crypto_shredding.py`) và `router.py`:
    - `POST /api/v1/tenants`
    - `POST /api/v1/memberships`
    - `POST /api/v1/privacy/parental-consents`
    - `POST /api/v1/privacy/data-erasure-requests`
  - Viết test `tests/test_privacy_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Học sinh dưới 16 tuổi chưa có `parental_consents` bị chặn kích hoạt lưu trữ PII nhạy cảm; xóa khóa mã hóa tạo `erasure_certificate` hợp lệ; các test Jinja cũ vẫn xanh với `legacy_users`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-07`.**

#### `TASK-08` — DB Models, Migration, Service & Router cho `B03 assets` (`assets`, `content_assets`, `question_assets`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `B03`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `assets`, `content_assets`, `question_assets`).
- **Việc cần làm:**
  - Tạo `app/modules/assets/internal/models.py`, migration Alembic (kèm `hybrid_read_root` / `hybrid_write_rls` theo Chương 4), `service.py` (kết nối `app/modules/assets/internal/manager.py` và `PDFEngineAdapter` để cắt `PREVIEW_PDF` 2 trang đầu), và `router.py`:
    - `POST /api/v1/assets/upload-sessions`
    - `POST /api/v1/assets/{id}/finalize`
    - `GET /api/v1/assets/{id}/signed-url`
  - Viết test `tests/test_assets_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Tài khoản `FREE` chỉ lấy được Signed URL của `PREVIEW_PDF`; truy cập `asset_id` chéo Tenant trả `404` do RLS.
  - **Dừng lại chờ duyệt sau khi xong `TASK-08`.**

#### `TASK-09` — DB Models, Migration, Service & Router cho `A01 ingestion`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `A01`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `sources`, `source_documents`, `document_versions`, `acquisition_jobs`, `pipeline_stage_runs`, `dead_letter_items`).
- **Việc cần làm:**
  - Tạo `app/modules/ingestion/internal/models.py`, migration Alembic (RLS + `GRANT` cho `ulp_app_rw` và `ulp_worker_ingestion`), `service.py` (kết nối `app/modules/ingestion/internal/backbone.py`, `app/platform/file_magic.py`, `app/platform/storage_objects.py`), và `router.py`:
    - `POST /api/v1/imports`
    - `GET /api/v1/imports/{id}`
    - `POST /api/v1/imports/{id}/retry`
    - `POST /api/v1/imports/{id}/cancel`
  - Viết test `tests/test_ingestion_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Upload trùng `checksum_sha256` trong cùng Tenant trả về `document_version` hiện có (`200 OK`); retry chỉ chạy lại từ chặng lỗi (`pipeline_stage_runs`).
  - **Dừng lại chờ duyệt sau khi xong `TASK-09`.**

---

### GIAI ĐOẠN 2: TRACK 1 — SPRINT 2 (TAXONOMY, CHƯƠNG TRÌNH, HỌC LIỆU, AI GATEWAY & AUDIT)

#### `TASK-10` — Hoàn thiện `/api/v1` Routers cho `B04 taxonomy` và `B06 curriculum`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `B04`, `B06`) và mã đã có trong `app/modules/taxonomy/` + `app/modules/curriculum/` (đã có `models.py`, migration `c9d0e1f2a3b4`, và `service.py`).
- **Việc cần làm:**
  - Tạo `app/modules/taxonomy/router.py`:
    - `GET /api/v1/taxonomy/nodes`
    - `POST /api/v1/taxonomy/nodes`
    - `POST /api/v1/taxonomy/nodes/{id}/versions`
  - Tạo `app/modules/curriculum/router.py`:
    - `POST /api/v1/curricula`
    - `POST /api/v1/curricula/{id}/versions`
    - `POST /api/v1/curriculum-mappings`
    - `GET /api/v1/curricula/{id}/coverage`
  - Gắn cả 2 router vào `app/main.py` và viết test API `tests/test_taxonomy_curriculum_api.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Truy vấn cây `ltree` (`<@`) `< 5ms`; nâng version nút không làm đứt liên kết; `CoverageReport` phản hồi `< 200ms`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-10`.**

#### `TASK-11` — DB Models, Migration, Immutability Trigger, Service & Router cho `B01 content`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `B01`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `content_items`, `content_versions`, `publications`, `access_grants`, trigger `prevent_version_immutability_mutation` đã vá `F-03`, cổng xuất bản 5 trục).
- **Việc cần làm:**
  - Tạo `app/modules/content/internal/models.py`, migration Alembic (có FK `DEFERRABLE` cho `current_version_id`, trigger chặn sửa dòng `PUBLISHED`/`is_immutable`, `hybrid_read_root` cho `content_items` và `hybrid_read_child` cho `content_versions`), `service.py` (gọi `app/modules/content/internal/library.py`), và `router.py`:
    - `POST /api/v1/content`
    - `POST /api/v1/content/{id}/versions`
    - `POST /api/v1/content-versions/{id}/publish`
  - Viết test `tests/test_content_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - `UPDATE` trực tiếp lên `content_versions` đã `PUBLISHED` bị DB trigger chặn bằng lỗi `IMMUTABLE_VIOLATION`; sửa bài đã publish sinh `version_no + 1` ở trạng thái `DRAFT`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-11`.**

#### `TASK-12` — DB Models, Migration, Service & Router cho `B02 authoring`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `B02`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `content_drafts`, `content_blocks`, `comment_threads`).
- **Việc cần làm:**
  - Tạo `app/modules/authoring/internal/models.py`, migration Alembic (`strict_tenant_rls`), `service.py` (kết nối `app/modules/authoring/internal/studio.py` kiểm tra cú pháp KaTeX và quyền sở hữu `asset_id` trong bảng `assets`), và `router.py`:
    - `POST /api/v1/content/{id}/drafts`
    - `PATCH /api/v1/drafts/{id}`
    - `POST /api/v1/drafts/{id}/validate`
  - Viết test `tests/test_authoring_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Chặn 100% lỗi KaTeX không đóng ngoặc, lệnh LaTeX cấm, hoặc nhúng `asset_id` thuộc Tenant khác.
  - **Dừng lại chờ duyệt sau khi xong `TASK-12`.**

#### `TASK-13` — DB Models, Migration, Service & Router cho `E01 ai_gateway`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `E01`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `ai_providers`, `ai_models`, `prompt_versions`, `ai_jobs`, `ai_traces`).
- **Việc cần làm:**
  - Tạo `app/modules/ai_gateway/internal/models.py`, migration Alembic, `service.py` (kết nối `app/modules/ai_gateway/internal/{key_ring.py, budget.py}` và `app/platform/circuit_breaker.py`), và `router.py`:
    - `POST /api/v1/ai/jobs`
    - `GET /api/v1/ai/jobs/{id}`
    - `GET /api/v1/ai/usage`
  - Viết test `tests/test_ai_gateway_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Gặp `429` tự động xoay sang key kế tiếp `< 50ms`; chạm trần `daily_budget_usd` của Tenant chặn 100% lời gọi mới.
  - **Dừng lại chờ duyệt sau khi xong `TASK-13`.**

#### `TASK-14` — DB Models, Partitioned Migration (`F-01`), Service & Router cho `G02 audit`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `G02`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng phân vùng `audit_events`, `takedown_cases`, hàm `ensure_monthly_partitions`, `secure_partition`), và `DDL_DRY_RUN_FINDINGS.md` (`F-01`).
- **Việc cần làm:**
  - Tạo `app/modules/audit/internal/models.py`, migration Alembic tạo bảng phân vùng `audit_events` + `takedown_cases`, áp dụng `partition_lockdown()` từ `app/platform/rls.py` và `REVOKE UPDATE, DELETE` + trigger bất biến.
  - Tạo `app/modules/audit/internal/service.py` (kết nối `diff.py` + `takedown.py` và đẩy sự kiện `rights.takedown.executed` vào `outbox_events`) và `router.py`:
    - `GET /api/v1/audit-logs`
    - `POST /api/v1/takedown-cases`
    - `POST /api/v1/takedown-cases/{id}/execute`
  - Viết test `tests/test_audit_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - `UPDATE`/`DELETE` trên `audit_events` bị từ chối ở cấp DB; truy vấn trực tiếp partition của Tenant khác bị chặn (`F-01`); thực thi takedown phát sự kiện `rights.takedown.executed` trong cùng giao dịch.
  - **Dừng lại chờ duyệt sau khi xong `TASK-14`.**

---

### GIAI ĐOẠN 3: TRACK 1 — SPRINT 3 (PARSERS, OCR LAYOUT, EXTRACTION, REVIEW & QUALITY)

#### `TASK-15` — DB Models, Migration, Service & Router cho `A05 parsers` và `A06 layout`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `A05`, `A06`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `parser_definitions`, `extraction_runs`, `extracted_pages`, `layout_regions`).
- **Việc cần làm:**
  - Tạo `models.py`, `service.py` và `router.py` cho `app/modules/parsers` (kết nối `internal/registry.py`) và `app/modules/layout` (kết nối `internal/geometry.py` + `PDFEngineAdapter`).
  - Tạo 1 migration Alembic cho 4 bảng `parser_definitions`, `extraction_runs`, `extracted_pages`, `layout_regions` kèm RLS và `GRANT` cho `ulp_worker_ingestion`.
  - Endpoints:
    - `POST /api/v1/documents`, `GET /api/v1/documents/{id}/versions`, `POST /api/v1/documents/{id}/extract`, `POST /api/v1/extraction-runs/{id}/reprocess`
    - `POST /api/v1/ocr/jobs`, `GET /api/v1/ocr/jobs/{id}`, `POST /api/v1/ocr/jobs/{id}/pages/{page}/reprocess`
  - Viết test `tests/test_parsers_layout_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Chặn file `.docx` có `word/document.xml > 25MB`; lưu `parser_version` và tọa độ `bbox` từng vùng trang.
  - **Dừng lại chờ duyệt sau khi xong `TASK-15`.**

#### `TASK-16` — DB Models, Migration, Service & Router cho `A07 extraction`, `A09 review` và `C02 quality`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `A07`, `A09`, `C02`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `content_candidates`, `question_candidates`, `review_tasks`, `review_decisions`, `source_rights`, `verification_runs`).
- **Việc cần làm:**
  - Tạo `models.py`, `service.py`, `router.py` cho:
    - `app/modules/extraction` (gọi `internal/splitter.py`, `internal/rubric_table.py`)
    - `app/modules/review` (gọi `internal/governance.py`)
    - `app/modules/quality` (gọi `internal/sympy_runner.py`, `internal/verifier.py`)
  - Tạo migration Alembic cho 6 bảng trên kèm CHECK constraint chặn Publish khi `rights_status IN ('PENDING_REVIEW', 'RESTRICTED', 'TAKEDOWN')` hoặc `verification_status != 'VERIFIED'`.
  - Viết test `tests/test_extraction_review_quality_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Câu hỏi có `rights_status = 'PENDING_REVIEW'` bị DB từ chối lệnh Publish với `CheckViolation`; mâu thuẫn nghiệm SymPy tự động tạo `review_task` mức `HIGH`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-16`.**

---

### GIAI ĐOẠN 4: TRACK 1 — SPRINT 4 (KHO CÂU HỎI, KHỬ TRÙNG, NHẬP ĐỀ, TÌM KIẾM & MA TRẬN)

#### `TASK-17` — DB Models, Migration (`questions` → `legacy_questions`), Views (`F-02`), Service & Router cho `C01 questions`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `C01`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `questions`, `question_versions`, `question_version_answers`, `question_options`, `question_stimuli`, view `v_public_question_versions`), `DDL_DRY_RUN_FINDINGS.md` (`F-02`, `F-03`).
- **Việc cần làm:**
  - Migration Alembic:
    - Đổi tên bảng `questions` cũ thành `legacy_questions` (cập nhật `__tablename__ = "legacy_questions"` trong `app/models.py` để code cũ không vỡ).
    - Tạo 5 bảng V11.3: `questions`, `question_versions`, `question_version_answers`, `question_options`, `question_stimuli` kèm `hybrid_read_root` / `hybrid_read_child`, trigger bất biến `prevent_version_immutability_mutation`, `REVOKE ALL` trên `question_version_answers` khỏi `ulp_exam_session_rw`, `ulp_worker_ingestion`, `ulp_system_scheduler`, `ulp_analytics_ro`, và `grading_read_question_answers` cho `ulp_worker_grading`.
    - Tạo view `v_public_question_versions` bằng `invoker_view()` (`security_invoker = true`).
  - Tạo `app/modules/questions/internal/models.py`, `service.py` (kết nối `internal/authoring.py`), `router.py`:
    - `POST /api/v1/questions`
    - `POST /api/v1/questions/{id}/versions`
    - `POST /api/v1/question-versions/{id}/validate`
  - Viết test `tests/test_questions_db.py` (bao gồm kiểm chứng bằng role `ulp_exam_session_rw` bị `42501` khi đọc `question_version_answers`).
- **Tiêu chí nghiệm thu (DoD):**
  - Hỗ trợ đủ 6 loại câu hỏi; sửa câu hỏi đã Publish sinh `version_no + 1`; `ulp_exam_session_rw` và `ulp_worker_ingestion` nhận lỗi `42501 InsufficientPrivilege` khi truy vấn `question_version_answers`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-17`.**

#### `TASK-18` — DB Models, Migration, Service & Router cho `A08 dedup` và `A04 exam_import`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `A08`, `A04`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `duplicate_clusters`, `duplicate_members`, `exam_import_sessions`, `provenance_records`).
- **Việc cần làm:**
  - Tạo `models.py`, `service.py`, `router.py` cho `app/modules/dedup` (kết nối `internal/hashing.py`, `internal/pairing.py`) và `app/modules/exam_import` (kết nối `internal/importer.py`).
  - Tạo migration Alembic cho 4 bảng trên.
  - Endpoints:
    - `POST /api/v1/dedup/match`, `GET /api/v1/duplicate-clusters/{id}`, `POST /api/v1/duplicate-clusters/{id}/decisions`
    - `POST /api/v1/exam-imports`, `GET /api/v1/exam-imports/{id}/structure`, `PATCH /api/v1/exam-imports/{id}/structure`, `POST /api/v1/exam-imports/{id}/approve`
  - Viết test `tests/test_dedup_exam_import_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Khử trùng 3 tầng phân biệt chính xác `H2SO4` vs `H2SO3` và gom đúng bài toán đổi số liệu vào `duplicate_clusters`; chỉ tạo `BRANDED_PDF` khi `rights_status IN ('OWNED', 'ALLOWED_DERIVATIVE')`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-18`.**

#### `TASK-19` — DB Models, Migration, Service & Router cho `B07 search` và `C03 frameworks`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `B07`, `C03`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `search_documents`, `collections`, `collection_items`, `assessment_frameworks`, `framework_versions`, `blueprints`, `scoring_scheme_versions`).
- **Việc cần làm:**
  - Tạo `models.py`, `service.py`, `router.py` cho `app/modules/search` (kết nối `internal/fts.py`, dùng hàm `f_unaccent` + GIN `tsvector`/`pg_trgm`) và `app/modules/frameworks` (kết nối `internal/blueprints.py`).
  - Tạo migration Alembic cho 7 bảng trên và seed 2 khung đề chuẩn THPTQG 2025+ (Toán 22 câu / 90 phút và Lý-Hóa-Sinh 28 câu / 50 phút).
  - Endpoints:
    - `GET /api/v1/search`, `POST /api/v1/collections`, `POST /api/v1/collections/{id}/items`
    - `POST /api/v1/assessment-frameworks`, `POST /api/v1/blueprints`, `POST /api/v1/blueprints/{id}/validate`
  - Viết test `tests/test_search_frameworks_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Tìm kiếm không dấu (`cong thuc bayes`) trả kết quả đúng và ẩn tức thì mục `TAKEN_DOWN`; `validate` và `ShortageReport` khớp chuẩn THPTQG 2025+.
  - **Dừng lại chờ duyệt sau khi xong `TASK-19`.**

---

### GIAI ĐOẠN 5: TRACK 1 — SPRINT 5 & 6 (BỐC ĐỀ, PHÒNG THI, FAST AUTOSAVE, CHẤM ĐIỂM & ĐÓNG 7 GATES)

#### `TASK-20` — DB Models, Migration, Service & Router cho `C04 assembly` và `C05 delivery`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `C04`, `C05`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `assessments`, `assessment_versions`, `assessment_items`, `assessment_answer_keys`, `attempts`).
- **Việc cần làm:**
  - Tạo `models.py`, `service.py`, `router.py` cho `app/modules/assembly` (kết nối `internal/assembler.py`) và `app/modules/delivery` (kết nối `internal/session.py`).
  - Migration Alembic tạo 5 bảng trên:
    - `REVOKE ALL` trên `assessment_answer_keys` khỏi `ulp_exam_session_rw`, `ulp_worker_ingestion`, `ulp_system_scheduler`, `ulp_analytics_ro`.
    - `grading_read_answer_keys` cho `ulp_worker_grading`.
    - `system_job_policy` cho `attempt_expiry` trên bảng `attempts`.
  - Endpoints:
    - `POST /api/v1/assessments`, `POST /api/v1/assessments/{id}/assemble`, `POST /api/v1/assessments/{id}/publish`
    - `POST /api/v1/exam-sessions/start`, `GET /api/v1/attempts/{id}/paper`, `POST /api/v1/attempts/{id}/submit`
  - Viết test `tests/test_assembly_delivery_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - `structure_snapshot_json` và response của `GET /api/v1/attempts/{id}/paper` chứa **0% dữ liệu đáp án**; cùng `random_seed` cho cùng đề; F5/đổi thiết bị giữ nguyên 100% thứ tự câu hỏi và hoán vị A/B/C/D.
  - **Dừng lại chờ duyệt sau khi xong `TASK-20`.**

#### `TASK-21` — DB Models, Partitioned Migration, Service & Router cho `C06 attempts` (`H-04` Fast Autosave)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `C06`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `responses`, bảng phân vùng `response_revisions`, `autosave_idempotency_registry`), `chapters/06_9_thuat_toan_loi.md` (Mục 6.4).
- **Việc cần làm:**
  - Tạo `app/modules/attempts/internal/models.py`, migration Alembic (áp dụng `partition_lockdown()` cho các partition của `response_revisions`, cấp quyền cho `ulp_exam_session_rw`).
  - Tạo `app/modules/attempts/internal/service.py` thực thi giao thức Idempotency-First bằng SQL thật (`FOR UPDATE` trên `responses`, đối chiếu `autosave_idempotency_registry` + `payload_hash` + `expected_response_version` qua `app/modules/attempts/internal/autosave.py`).
  - Tạo `app/modules/attempts/router.py`:
    - `PUT /api/v1/attempts/{id}/responses/{item_id}`
    - `GET /api/v1/attempts/{id}/sync`
  - Viết test Chaos/Concurrency `tests/test_attempts_autosave_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Vượt qua toàn bộ 6 kịch bản `H-04` trên PostgreSQL thật (`IDEMPOTENT_REPLAY`, `422 PAYLOAD_HASH_MISMATCH`, `409 ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED`, `403 ATTEMPT_DEADLINE_EXPIRED`, `409 OPTIMISTIC_VERSION_CONFLICT`, `409 STALE_CLIENT_SEQUENCE`).
  - **Dừng lại chờ duyệt sau khi xong `TASK-21`.**

#### `TASK-22` — DB Models, Migration, Service & Router cho `C07 grading` + Kiểm định `GATE-01, 02, 03, 04, 06a, 07`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `C07`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `grades`, `grading_details`, `rubric_versions`, `appeals`).
- **Việc cần làm:**
  - Tạo `app/modules/grading/internal/models.py`, migration Alembic (cấp quyền cho `ulp_worker_grading`), `service.py` (chạy dưới role/giao dịch chấm điểm, gọi `app/modules/grading/internal/scoring.py`), và `router.py`:
    - `POST /api/v1/attempts/{id}/grade`
    - `POST /api/v1/grades/{id}/release`
    - `POST /api/v1/grades/{id}/appeals`
  - Viết test tích hợp đầu-cuối (End-to-End Track 1: tạo câu hỏi → duyệt quyền → bốc đề → mở phòng thi → autosave → nộp bài → chấm điểm → phúc khảo/regrade) trong `tests/test_grading_e2e_db.py`.
- **Tiêu chí nghiệm thu (DoD):**
  - Chấm điểm chuẩn THPTQG 2025+ chính xác 100%; Regrade tạo dòng `grades` mới với `revision_no + 1` và đặt dòng cũ `is_current = FALSE`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-22`.**

#### `TASK-23` — Script Backfill Dữ Liệu Thực Tế Từ Bảng `legacy_*` Sang Bảng V11.3 (`W0.6`)
- **Đọc trước:** `docs/architecture/BACKFILL_MAP.md` và `app/ops/backfill_mapper.py`.
- **Việc cần làm:**
  - Viết trình chạy backfill idempotent theo lô 500 dòng (`app/ops/run_backfill.py` hoặc CLI command) theo đúng thứ tự phụ thuộc trong `BACKFILL_MAP.md` mục 6: `assets` → `sources`/`document_versions` → `taxonomy` → `curriculum` → `content` → `questions` (Toán & Vật lí lớp 10–12 trước) → `dedup` → `assessments`.
  - Viết test kiểm chứng backfill `tests/test_backfill_execution.py`: số dòng nguồn = số dòng đích + số dòng bị loại có lý do; chạy lần 2 sinh `0` dòng mới.
- **Tiêu chí nghiệm thu (DoD):**
  - Backfill idempotent 100%, đối soát khớp số dòng và `content_hash` NFC.
  - **Dừng lại chờ duyệt sau khi xong `TASK-23`.**

---

### GIAI ĐOẠN 6: GIAO DIỆN WEB NEXT.JS CHO 6 WORKSPACES (KẾT NỐI `frontend/src/lib/*` VỚI `/api/v1/*`)

#### `TASK-24` — Khởi tạo Next.js App Router & Xây dựng **Workspace 2: High-Resilience Online Exam Room** (`/phong-thi/[attempt_id]`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (Mục 3.8 — Workspace 2) và các file đã có trong `frontend/src/lib/` (`exam-autosave-store.ts`, `exam-countdown.ts`, `exam-input-policy.ts`, `exam-proctor.ts`, `exam-submission.ts`, `server-clock.ts`, `ui-state-machine.ts`).
- **Việc cần làm:**
  - Cấu hình Next.js (App Router, TypeScript, TailwindCSS, KaTeX) trong `frontend/`.
  - Xây dựng các component chuẩn cho 6 trạng thái UI bắt buộc (`Loading Skeleton`, `Empty State`, `Error Boundary`, `Locked Pro Preview`, `Offline Retry Banner`, `Sequence Conflict Modal`).
  - Xây dựng trang `/phong-thi/[attempt_id]` (Sticky Topbar đồng hồ Server, Phần I ABCD + gạch phương án nhiễu, Phần II Đúng/Sai 4 ý a/b/c/d có cảnh báo vàng khi chọn thiếu ý, Phần III 4 ô ký tự chuẩn phiếu tô THPTQG, Question Palette, kết nối `ExamAutosaveStore` Dexie và API `/api/v1/attempts/*`).
- **Tiêu chí nghiệm thu (DoD):**
  - `npm test` và `tsc --noEmit` và `next build` trong `frontend/` chạy sạch 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-24`.**

#### `TASK-25` — Xây dựng **Workspace 1: Learner Study & Practice Hub** (`/hoc-lieu/[slug]`, `/lo-trinh`, `/so-tay-cau-sai`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (Mục 3.8 — Workspace 1), `frontend/src/lib/workspace1-study-hub.ts`, `frontend/src/lib/katex-security.ts`, `app/platform/branding.py`.
- **Việc cần làm:**
  - Xây dựng bố cục 3 cột chuẩn VietJack nâng cấp (Cột trái 260px cây mục lục 4 cấp gắn chấm màu Mastery; Cột giữa 760px hiển thị các khối lý thuyết + 4 Tab bài tập tương tác; Cột phải 320px khung Gia sư AI 3 chế độ + Sổ tay câu sai) kèm bộ nhận diện thương hiệu **H2Edu** (`Học thông minh - Thi bứt phá`).
  - Cài đặt `Locked Preview` cho tài liệu `PRO` khi user ở gói `FREE` (chỉ tải `PREVIEW_PDF`, không lộ URL gốc xuống DOM).
- **Tiêu chí nghiệm thu (DoD):**
  - Render KaTeX với `throwOnError: false, trust: false`; `npm test` và `tsc --noEmit` xanh 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-25`.**

#### `TASK-26` — Xây dựng **Workspace 3 (Bounding-Box Reviewer)** & **Workspace 4 (VietJack-Style Block Studio)**
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (Mục 3.8 — Workspace 3 & 4), `frontend/src/lib/bbox-reviewer.ts`, `frontend/src/lib/block-studio-linter.ts`.
- **Việc cần làm:**
  - Xây dựng `/studio/imports/[run_id]/review` (Canvas PDF + lớp phủ SVG `bbox` kéo-thả cắt lại hình bên trái; Inspector câu hỏi + cảnh báo `DISPUTED_ANSWER` từ SymPy + khóa nút `Publish` khi `rights_status` chưa hợp lệ bên phải).
  - Xây dựng `/studio/content/[id]` và `/studio/questions/new` (Block Studio `/congthuc`, `/phuongphap`, `/chuy`, `/vidu` + Live KaTeX Preview + Pre-Submit Validator).
- **Tiêu chí nghiệm thu (DoD):**
  - `npm test` và `tsc --noEmit` xanh 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-26`.**

#### `TASK-27` — Xây dựng **Workspace 5 (Matrix Exam Builder & Teacher LMS)** & **Workspace 6 (Admin Ops & Takedown Console)**
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (Mục 3.8 — Workspace 5 & 6), `frontend/src/lib/matrix-builder.ts`, `frontend/src/lib/workspace6-admin-ops.ts`.
- **Việc cần làm:**
  - Xây dựng `/teacher/exam-builder` và `/teacher/classes/[id]` (bảng ma trận 2 chiều báo đỏ ô thiếu câu `ShortageReport`, bảng điểm lớp, phân tích Top 5 câu sai + phương án nhiễu, khung Teacher AI Copilot).
  - Xây dựng `/admin/ops`, `/admin/sources`, `/admin/takedowns` (quản lý nguồn/Drive pairing, giám sát Outbox/DLQ Replay, nút `Execute Legal Takedown` bắt buộc nhập `reason >= 15` ký tự).
- **Tiêu chí nghiệm thu (DoD):**
  - Đủ 6 Workspaces theo đặc tả Chương 3.8; `npm test` và `tsc --noEmit` xanh 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-27`.**

---

### GIAI ĐOẠN 7: TRACK 2 — INTELLIGENT & LMS (SPRINT 7–9)

#### `TASK-28` — Sprint 7 (Phần 1): DB Models, Migration, Service & Router cho `B05 knowledge_graph` và `D01 dashboard`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `B05`, `D01`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `knowledge_edges`, `graph_validation_runs`, `activity_events`, `learner_dashboard_projections`).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic (policy `hybrid_read_policy` trên `knowledge_edges` kiểm tra cả `source_node_id` và `target_node_id` đang `is_active IS TRUE` theo `H-01`), `service.py` (kết nối `app/modules/knowledge_graph/internal/dag.py` và `app/modules/dashboard/internal/projection.py`), và `router.py` cho `knowledge_graph` + `dashboard`.
- **Tiêu chí nghiệm thu (DoD):**
  - Thêm cạnh `PREREQUISITE` tạo chu trình bị chặn với `422 PREREQUISITE_CYCLE_DETECTED`; `knowledge_edges` ẩn khi 1 trong 2 đầu mút `is_active = FALSE`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-28`.**

#### `TASK-29` — Sprint 7 (Phần 2): DB Models, Migration, Service & Router cho `D02 learning_path`, `D03 practice`, `D05 error_notebook`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `D02`, `D03`, `D05`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `learning_plans`, `plan_tasks`, `practice_sessions`, `practice_responses`, `error_notes`, `remediation_tasks`).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic, `service.py` (kết nối `planner.py`, `engine.py`, `notebook.py`), và `router.py` cho 3 module `learning_path`, `practice`, `error_notebook`.
- **Tiêu chí nghiệm thu (DoD):**
  - `error_notes` chỉ chuyển sang `RESOLVED` khi `consecutive_correct_count >= 2`; nhiệm vụ trong `plan_tasks` luôn có `rationale_text` và không ghi đè bài tập bắt buộc của giáo viên.
  - **Dừng lại chờ duyệt sau khi xong `TASK-29`.**

#### `TASK-30` — Sprint 8: DB Models, Migration, Service & Router cho `F01 classes`, `F02 assignments`, `F03 gradebook`, `F06 notifications`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `F01`, `F02`, `F03`, `F06`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `groups`, `classes`, `class_members`, `assessment_assignments`, `gradebook_projections`, `notifications`, `notification_deliveries`), và `IMPLEMENTATION_PLAN_V11_3.md` (Quyết định #7: `notification_deliveries` có `tenant_id` và policy RLS chuẩn).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic, `service.py`, `router.py` cho 4 module `classes`, `assignments`, `gradebook`, `notifications` (kết nối Pure Core tương ứng).
- **Tiêu chí nghiệm thu (DoD):**
  - Học sinh rời lớp được Soft-Delete (`status = 'INACTIVE'`), giữ nguyên lịch sử bài thi; phát lại sự kiện `attempt.graded` 3 lần chỉ sinh đúng 1 thông báo nhờ `dedup_key UNIQUE`.
  - **Dừng lại chờ duyệt sau khi xong `TASK-30`.**

#### `TASK-31` — Sprint 9: DB Models, Migration, Service & Router cho `D04 flashcards`, `E02 tutor`, `E04 explanations` (`GATE-08`)
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `D04`, `E02`, `E04`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `flashcard_decks`, `flashcards`, `revision_schedules`, `tutor_sessions`, `tutor_messages`, `retrieval_traces`, `explanation_runs`).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic, `service.py`, `router.py` cho `flashcards` (kết nối `internal/fsrs.py`), `tutor` (kết nối `internal/guard.py`, kiểm tra bảng `attempts` chặn `403 EXAM_SESSION_ACTIVE` khi học sinh đang thi `IN_PROGRESS`, lọc RAG `publication_status = 'PUBLISHED' AND is_immutable IS TRUE`), và `explanations` (kết nối `internal/explainer.py` + `SymPy` sandbox).
- **Tiêu chí nghiệm thu (DoD):**
  - Đóng `GATE-08`: chặn 100% yêu cầu hỏi AI Tutor khi đang có `attempt` `IN_PROGRESS`, chặn lộ đáp án ở `HINT_MODE`, FSRS-5 khớp vector chuẩn.
  - **Dừng lại chờ duyệt sau khi xong `TASK-31`.**

---

### GIAI ĐOẠN 8: TRACK 3 — EXPANSION, ANALYTICS, BILLING & HARDENING GATES (SPRINT 10–14)

#### `TASK-32` — Sprint 10: DB Models, Migration, Service & Router cho `A02 crawler`, `A03 drive_sync`, `E03 generation`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `A02`, `A03`, `E03`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `crawl_urls`, `crawl_fetches`, `source_connections`, `source_sync_cursors`, `file_pairing_candidates`, `generation_requests`).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic, `service.py`, `router.py` cho `crawler` (kết nối `internal/ssrf.py`, `internal/engine.py`), `drive_sync` (kết nối `internal/safe_archive.py`), và `generation` (kết nối `internal/gap_filler.py`, bắt buộc đẩy qua `C02` + `A09` với `acquisition_method = 'AI_GENERATED'`).
- **Tiêu chí nghiệm thu (DoD):**
  - 0% câu hỏi AI sinh ra đi thẳng vào `PUBLISHED`; bộ test SSRF và Zip Bomb qua 100%.
  - **Dừng lại chờ duyệt sau khi xong `TASK-32`.**

#### `TASK-33` — Sprint 11: DB Models, Migration, Service & Router cho `D06 mastery`, `E05 recommendations`, `E06 teacher_copilot`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `D06`, `E05`, `E06`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `mastery_estimates`, `question_irt_parameters`, `recommendations`, `teacher_assist_jobs`).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic, `service.py`, `router.py` cho `mastery` (kết nối `internal/irt.py` 4 bậc theo `platform_manifest.yaml`), `recommendations` (`internal/ranker.py`), và `teacher_copilot` (`internal/copilot.py`).
- **Tiêu chí nghiệm thu (DoD):**
  - Trả về `INSUFFICIENT_DATA` khi học sinh làm `< 20` câu hoặc `SE(theta) >= 0.35`; `teacher_copilot` chỉ đọc dữ liệu thuộc đúng `class_id` của giáo viên trong Tenant.
  - **Dừng lại chờ duyệt sau khi xong `TASK-33`.**

#### `TASK-34` — Sprint 12: DB Models, Migration, Service & Router cho `G04 analytics`, `G05 billing`, `G06 vertical_packs`
- **Đọc trước:** `chapters/03_dac_ta_47_modules_va_6_workspaces.md` (dòng `G04`, `G05`, `G06`), `chapters/04_postgresql_ddl_rls_grants.sql` (bảng `analytics_events`, `daily_metric_rollups`, `subscription_plans`, `subscriptions`, `usage_ledger`, `payment_webhooks`, `vertical_packs`, `locale_bundles`), và `IMPLEMENTATION_PLAN_V11_3.md` (Quyết định #7: `payment_webhooks` chỉ cấp `INSERT` cho `ulp_app_rw`).
- **Việc cần làm:**
  - Tạo `models.py`, migration Alembic, `service.py`, `router.py` cho `analytics` (kết nối `internal/warehouse.py`, job `metric_rollup` chạy dưới `ulp_system_scheduler`), `billing` (kết nối `internal/ledger.py`, kiểm tra HMAC payload đã ký, phát sự kiện `subscription.updated`), và `vertical_packs` (`internal/packs.py`).
- **Tiêu chí nghiệm thu (DoD):**
  - Toàn bộ 123 bảng logic của Chương 4 đã có mặt trong chuỗi migration Alembic của ứng dụng; webhook thanh toán gửi trùng nhiều lần chỉ cộng hạn mức đúng 1 lần.
  - **Dừng lại chờ duyệt sau khi xong `TASK-34`.**

#### `TASK-35` — Kiểm định Toàn diện Schema Thực Tế Đối Chiếu Chương 4 (`Gate 2b` trên DB Ứng Dụng)
- **Đọc trước:** `tests/security/test_ddl_dry_run.py`, `tests/security/grants_manifest.csv`, `app/ops/ddl_rls_audit.py`, `app/platform/rls.py`.
- **Việc cần làm:**
  - Viết bộ test `tests/security/test_alembic_matches_chapter4.py` chạy `alembic upgrade head` trên DB test rồi thực thi toàn bộ các kiểm chứng của `test_ddl_dry_run.py`:
    - 100% bảng có `tenant_id` bật `ENABLE` + `FORCE ROW LEVEL SECURITY` và có policy dùng `(SELECT current_app_tenant_id())`.
    - 0 role có `rolbypassrls`.
    - Quyền thực tế trong `information_schema.role_table_grants` khớp `tests/security/grants_manifest.csv`.
    - Chạy `tests/security/verify_rls_hardening.sql` trực tiếp trên schema do Alembic tạo ra.
- **Tiêu chí nghiệm thu (DoD):**
  - Schema do chuỗi Alembic migrations tạo ra đạt 100% tính chất bảo mật như bản DDL Chương 4.
  - **Dừng lại chờ duyệt sau khi xong `TASK-35`.**

#### `TASK-36` — Diễn tập Release Gates Cuối (`GATE-01` .. `GATE-10`), `mypy --strict` & Đóng Băng Phát Hành V11.3
- **Đọc trước:** `chapters/07_08_ci_cd_lo_trinh_waves_gates.md`, `chapters/09_10_qa_k6_rls_tests_14_runbooks.md`, `app/ops/release_gates.py`.
- **Việc cần làm:**
  - Chạy kiểm tra kiểu tĩnh `mypy` trên `app/contracts`, `app/platform`, `app/ops` và `app/modules`.
  - Chạy bộ kiểm toán SBOM/License (`app/ops/sbom_license_audit.py`), PDP compliance (`app/ops/pdp_compliance.py`), PITR tombstones (`app/ops/pitr_tombstones.py`), và tổng hợp `GateEvidenceRecord` cho `GATE-01..GATE-10`.
- **Tiêu chí nghiệm thu (DoD):**
  - Toàn bộ 6 cổng CI (`ruff`, `lint-imports`, `check_gate_ids.sh`, `alembic check`, `pytest`, `sbom_license_audit`) và `frontend` (`tsc --noEmit`, `vitest`) xanh 100%.
  - **Báo cáo nghiệm thu tổng thể cho chủ dự án.**
