# Thiết Kế Điều Phối Song Song: Claude + Gemini 4 Hydrogen (ULP V11.3)

> **Tài liệu Chiến lược Điều phối Đa Mô Hình & Phân Luồng Không Đụng Độ (`Zero-Conflict Parallelism`).**
> Đi kèm công cụ CLI tự động: `python scripts/orchestrate.py`
> Nền tảng: `docs/architecture/CLAUDE_CODE_SEQUENTIAL_PLAN.md` (`TASK-01` .. `TASK-36`).

---

## 1. TRẢ LỜI CÂU HỎI: CÓ NÊN DÙNG FABLE ĐỂ ĐIỀU PHỐI KHÔNG HAY CHỌN MODEL NÀO CỦA CLAUDE?

### 1.1. Nguyên tắc chọn Model tối ưu Chi phí / Chất lượng / Tốc độ

Không nên dùng một model đắt nhất (như Fable hoặc Opus) để gõ toàn bộ code từ đầu đến cuối, vì 70% khối lượng còn lại là **tầng Persistence (`models.py`, `service.py`), REST Router (`router.py`) và giao diện Next.js (`frontend/src/app/`)** đã có sẵn đặc tả cột SQL và hàm lõi Pure Core.

Cấu hình **Tam Giác Điều Phối (3-Tier Orchestration)** tối ưu nhất cho dự án này như sau:

| Vai trò | Model Khuyên Dùng | Khi nào dùng & Lý do kỹ thuật |
| :--- | :--- | :--- |
| **1. Tổng Điều Phối & Cổng Kiểm Duyệt (`Orchestrator / Gatekeeper`)** | **Tôi (Antigravity - tại khung chat này)** *hoặc* **Claude Fable** (chỉ bật ở cuối mỗi Đợt/Wave) | • **Không tốn quota Fable để viết code thường.**<br>• Chỉ dùng ở **đầu đợt** (chạy `python scripts/orchestrate.py prepare WAVE-x` để chia nhánh) và **cuối đợt** (kiểm tra chéo PR của Claude + Gemini, gộp `app/model_registry.py` + `app/main.py`, chạy `ruff` + `lint-imports` + `alembic check` + `pytest`, rồi merge vào `main`). |
| **2. Luồng A — `Claude Worker` (Chuyên trách DB Migration, RLS, Bảo mật & PDF Engine)** | • **Claude Opus (hoặc Fable):** Dùng cho **6 Task nhạy cảm nhất về bảo mật/cốt lõi**: `TASK-02` (PDF Redaction), `TASK-06` (`F05 access` RBAC/Zero-IDOR), `TASK-07` (`F04 privacy` Vault/Consent), `TASK-14` (`G02 audit` Partition RLS `F-01`), `TASK-17` (`C01 questions` Answer-Key Revoke & Invoker View `F-02`), `TASK-21` (`C06 attempts` `FOR UPDATE` Autosave).<br>• **Claude Sonnet:** Dùng cho **toàn bộ các Task Migration/DB còn lại** (nhanh gấp 2.5 lần, tiết kiệm quota, độ chính xác SQLAlchemy/Alembic rất cao khi đã có file `.sql` Chương 4). | • Chuỗi Alembic (`alembic/versions/`) bắt buộc chỉ có **1 đầu (`single head`)**. Do đó **chỉ một mình Luồng Claude được phép tạo file migration Alembic** và sửa `app/model_registry.py` / `app/main.py`. |
| **3. Luồng B — `Gemini 4 Hydrogen Worker` (Chuyên trách Module ORM/Service/Router cô lập, Next.js 6 Workspaces & Backfill)** | **Gemini 4 Hydrogen** (chạy song song trên worktree riêng) | • Gemini 4 Hydrogen cực mạnh khi làm việc trong **phạm vi thư mục cô lập rõ ràng**:<br>  1) Viết `router.py` + `service.py` cho các module đã có bảng (`outbox`, `taxonomy`, `curriculum`),<br>  2) Dựng sẵn `internal/models.py` + `internal/service.py` + `router.py` cho đợt kế tiếp (không đụng `alembic/versions/`),<br>  3) Xây dựng toàn bộ **6 Workspaces Next.js (`frontend/src/app/**`)**,<br>  4) Viết công cụ **Backfill (`app/ops/run_backfill.py`)** và bộ test **Gate 2b**. |

---

## 2. NGUYÊN TẮC CỐT LÕI ĐỂ CLAUDE VÀ GEMINI CHẠY SONG SONG MÀ KHÔNG BAO GIỜ XUNG ĐỘT (`0% MERGE CONFLICT`)

Trong các đợt trước, xung đột xảy ra vì cả 2 bên cùng sửa `.importlinter`, cùng tạo module trùng tên (`audit`), hoặc cùng sửa một file. Nay toàn bộ 52 contract trong `.importlinter` đã khóa xong 100%, ta áp dụng **Luật Phân Làn Cứng (Strict Lane Ownership)**:

| Tệp / Thư mục | Luồng A (`Claude`) | Luồng B (`Gemini 4 Hydrogen`) | Ghi chú |
| :--- | :---: | :---: | :--- |
| `alembic/versions/*.py` | **ĐƯỢC PHÉP (Độc quyền)** | **CẤM TUYỆT ĐỐI** | Giữ chuỗi migration 1 đầu duy nhất (`single head`) |
| `app/model_registry.py` | **ĐƯỢC PHÉP (Độc quyền)** | **CẤM TUYỆT ĐỐI** | Tránh conflict ở file 15 dòng |
| `app/main.py` | **ĐƯỢC PHÉP (Độc quyền)** | **CẤM TUYỆT ĐỐI** | Claude (hoặc Điều phối viên) mount `router.py` của cả 2 bên ở cuối Wave |
| `.importlinter` | **KHÔNG SỬA** | **KHÔNG SỬA** | Đã đủ 52 contracts cho toàn bộ 47 modules |
| `app/infrastructure/pdf_engine.py` & 8 file PDF cũ | **ĐƯỢC PHÉP (Wave 1)** | **CẤM TUYỆT ĐỐI** | Claude đóng dứt điểm `ADR-010` PR C |
| `frontend/**` (Next.js 6 Workspaces) | **CẤM TUYỆT ĐỐI** | **ĐƯỢC PHÉP (Độc quyền)** | Gemini sở hữu toàn bộ UI 6 Workspaces |
| `app/modules/<module_A>/**` | **ĐƯỢC PHÉP** | **CẤM** | Mỗi module trong một Wave chỉ giao cho 1 agent duy nhất |
| `app/modules/<module_B>/**` | **CẤM** | **ĐƯỢC PHÉP** | Gemini viết sẵn `models.py`, `service.py`, `router.py` + test |

---

## 3. BẢNG PHÂN CÔNG 8 ĐỢT SONG SONG (`WAVE-1` .. `WAVE-8`) BAO PHỦ TOÀN BỘ 36 TASKS

Mỗi **Wave** gồm 2 gói việc chạy **đồng thời** trên 2 worktree riêng (`claude-wX` và `gemini-wX`). Trong mỗi nhánh, từng agent vẫn phải làm lần lượt từng task nhỏ theo đúng quy tắc kỷ luật.

### `WAVE-1`: Đóng Kín Wave 0 PDF Engine (`PR C`) + Mở API Cho 3 Module Đã Có Bảng (`G03`, `B04`, `B06`)
- **Luồng A — Claude (`Sonnet` cho `TASK-01, 03` / `Opus` cho `TASK-02`):**
  - Thực hiện lần lượt **`TASK-01` → `TASK-02` → `TASK-03`**:
    - Chuyển 8 file ứng dụng (`pdf_preview.py`, `anthologies.py`, `library_split.py`, `figures.py`, `essay_exam.py`, `normalize.py`, `cli.py`, `exam_pdf.py`, `universal_ingestor.py`) và 9 file test khỏi `pymupdf`/`fitz`.
    - Xóa `pymupdf>=1.24` khỏi `requirements.txt` và thêm kiểm tra cấm `fitz`/`pymupdf` vào `tests/test_architecture.py`.
- **Luồng B — Gemini 4 Hydrogen (chạy song song, 0 đụng độ file):**
  - Thực hiện lần lượt **`TASK-04` → `TASK-10`** (vì 3 module `outbox`, `taxonomy`, `curriculum` **đã có sẵn bảng DB và migration trên `main`**!):
    - `TASK-04`: Viết `app/modules/outbox/internal/service.py` + `app/modules/outbox/router.py` (`/api/v1/admin/outbox/stats`, `/api/v1/admin/dlq`, `/api/v1/admin/dlq/{id}/replay`) + `tests/test_outbox_api.py`.
    - `TASK-10`: Viết `app/modules/taxonomy/router.py` + `app/modules/curriculum/router.py` + `tests/test_taxonomy_curriculum_api.py`. *(Không sửa `app/main.py`; bước gộp Wave 1 sẽ gắn 3 router này vào `app/main.py`)*.

---

### `WAVE-2`: Sprint 1 Core Security & Governance (`G01`, `F05`, `F04`) + Sprint 1 Assets & Ingestion (`B03`, `A01`)
- **Luồng A — Claude (`Opus` hoặc `Fable` — nhạy cảm bảo mật cao):**
  - Thực hiện lần lượt **`TASK-05` → `TASK-06` → `TASK-07`** (kèm tạo Alembic migration cho cả Sprint 1):
    - `TASK-05`: `G01 config` (`platform_settings`, `feature_flags`, service, router).
    - `TASK-06`: `F05 access` (`roles`, `permissions`, `role_permissions`, `get_tenant_context`, Zero-IDOR suite).
    - `TASK-07`: `F04 privacy` (đổi `users` → `legacy_users`, tạo `users`, `memberships`, `parental_consents`, `user_pii_encryption_keys`, `erasure_certificates`).
- **Luồng B — Gemini 4 Hydrogen:**
  - Thực hiện lần lượt phần `models.py` + `service.py` + `router.py` + unit/service tests của **`TASK-08` (`B03 assets`) → `TASK-09` (`A01 ingestion`)** + khởi tạo khung Next.js App Router cho **`TASK-24` (Phần khung UI 6 trạng thái chuẩn trong `frontend/`)**:
    - Viết `app/modules/assets/{internal/models.py, internal/service.py, router.py}` và `app/modules/ingestion/{internal/models.py, internal/service.py, router.py}` (bám sát cột Chương 4, không tạo file trong `alembic/versions/`).
    - Cuối Wave 2: Điều phối viên/Claude tạo 1 migration Alembic gộp bảng của `B03` + `A01` và đăng ký vào `app/model_registry.py`.

---

### `WAVE-3`: Sprint 2 Content, Authoring, AI Gateway, Audit (`B01`, `B02`, `E01`, `G02`) + Workspace 2 Phòng Thi Next.js (`TASK-24`)
- **Luồng A — Claude (`Sonnet` cho `TASK-11, 12, 13` / `Opus` cho `TASK-14`):**
  - Tạo migration bổ sung cho `B03 + A01` (từ Wave 2), sau đó thực hiện lần lượt **`TASK-11` (`B01 content`) → `TASK-12` (`B02 authoring`) → `TASK-13` (`E01 ai_gateway`) → `TASK-14` (`G02 audit` phân vùng + `F-01`)**.
- **Luồng B — Gemini 4 Hydrogen:**
  - Thực hiện **`TASK-24` (Hoàn thiện Workspace 2: High-Resilience Online Exam Room `/phong-thi/[attempt_id]` trong `frontend/`)** + viết sẵn `internal/models.py`, `internal/service.py`, `router.py` cho **`TASK-15` (`A05 parsers`, `A06 layout`)**.

---

### `WAVE-4`: Sprint 3 & 4 Extraction, Review, Quality, Questions (`A05..A09`, `C02`, `C01`) + Workspace 1 Study Hub (`TASK-25`)
- **Luồng A — Claude (`Sonnet` cho `TASK-15, 16` / `Opus` cho `TASK-17`):**
  - Tạo migration Alembic và hoàn thiện **`TASK-15` (`A05, A06`) → `TASK-16` (`A07 extraction`, `A09 review`, `C02 quality`) → `TASK-17` (`C01 questions`: đổi `questions` → `legacy_questions`, tạo 5 bảng câu hỏi, `REVOKE ALL` trên `question_version_answers`, tạo `v_public_question_versions` với `security_invoker = true` `F-02`)**.
- **Luồng B — Gemini 4 Hydrogen:**
  - Thực hiện **`TASK-25` (Workspace 1: Learner Study & Practice Hub `/hoc-lieu/[slug]`, `/lo-trinh`, `/so-tay-cau-sai`)** + viết sẵn `internal/models.py`, `internal/service.py`, `router.py` cho **`TASK-18` (`A08 dedup`, `A04 exam_import`)** và **`TASK-19` (`B07 search`, `C03 frameworks`)**.

---

### `WAVE-5`: Sprint 4–6 Exam Assembly, Delivery, Fast Autosave & Grading (`A08, A04, B07, C03..C07`) + Workspace 3 & 4 (`TASK-26`)
- **Luồng A — Claude (`Sonnet` cho `TASK-18, 19, 20, 22` / `Opus` cho `TASK-21`):**
  - Tạo migration Alembic và hoàn thiện **`TASK-18` (`A08, A04`) → `TASK-19` (`B07, C03`) → `TASK-20` (`C04 assembly`, `C05 delivery`) → `TASK-21` (`C06 attempts` `FOR UPDATE` Fast Autosave `H-04`) → `TASK-22` (`C07 grading` + E2E Track 1)**.
- **Luồng B — Gemini 4 Hydrogen:**
  - Thực hiện **`TASK-26` (Workspace 3 Bounding-Box Reviewer & Workspace 4 VietJack-Style Block Studio trong `frontend/`)** + **`TASK-23` (Viết công cụ CLI Backfill `app/ops/run_backfill.py` theo `BACKFILL_MAP.md`)**.

---

### `WAVE-6`: Thực Thi Backfill + Track 2 Sprint 7–8 (`B05, D01..D03, D05, F01..F03, F06`) + Workspace 5 & 6 (`TASK-27`)
- **Luồng A — Claude (`Sonnet`):**
  - Chạy kiểm chứng **`TASK-23` (Backfill trên DB)**, sau đó thực hiện **`TASK-28` (`B05 knowledge_graph`, `D01 dashboard`) → `TASK-29` (`D02 learning_path`, `D03 practice`, `D05 error_notebook`) → `TASK-30` (`F01 classes`, `F02 assignments`, `F03 gradebook`, `F06 notifications`)**.
- **Luồng B — Gemini 4 Hydrogen:**
  - Thực hiện **`TASK-27` (Workspace 5 Matrix Exam Builder & Teacher LMS + Workspace 6 Admin Ops & Takedown Console trong `frontend/`)** + viết sẵn `internal/models.py`, `internal/service.py`, `router.py` cho **`TASK-31` (`D04 flashcards`, `E02 tutor`, `E04 explanations`)**.

---

### `WAVE-7`: Track 2 Sprint 9 (`GATE-08`) & Track 3 Sprint 10–12 (`A02, A03, E03, D06, E05, E06, G04..G06`)
- **Luồng A — Claude (`Opus` cho `TASK-31` Tutor Exam Lock / `Sonnet` cho `TASK-32..34`):**
  - Tạo migration Alembic và hoàn thiện **`TASK-31` (`D04, E02, E04` — đóng `GATE-08`) → `TASK-32` (`A02 crawler`, `A03 drive_sync`, `E03 generation`) → `TASK-33` (`D06 mastery`, `E05 recommendations`, `E06 teacher_copilot`) → `TASK-34` (`G04 analytics`, `G05 billing`, `G06 vertical_packs`)**.
- **Luồng B — Gemini 4 Hydrogen:**
  - Thực hiện **`TASK-35` (Viết bộ kiểm định tự động `tests/security/test_alembic_matches_chapter4.py` đối chiếu toàn bộ bảng/RLS/Grants của Alembic với Chương 4 & `grants_manifest.csv`)** + chuẩn bị bộ kiểm tra **`TASK-36` (`mypy` & Release Gates harness)**.

---

### `WAVE-8`: Tổng Kiểm Định `Gate 2b` Trên DB Ứng Dụng & Đóng Băng Phát Hành `GATE-01..GATE-10`
- **Điều phối viên (`Antigravity` hoặc `Claude Fable`):**
  - Chạy `alembic upgrade head` cho toàn bộ 123 bảng logic, thực thi `tests/security/test_alembic_matches_chapter4.py` (`TASK-35`) và bộ nghiệm thu 11 cổng `GATE-01..GATE-10` (`TASK-36`).

---

## 4. HƯỚNG DẪN SỬ DỤNG CÔNG CỤ ĐIỀU PHỐI TỰ ĐỘNG (`scripts/orchestrate.py`)

Công cụ `scripts/orchestrate.py` đã được tích hợp sẵn trong repository để tự động hóa việc chia việc, tạo nhánh/worktree, sinh câu lệnh giao việc (prompt) và kiểm tra vi phạm phạm vi file của từng agent:

1. **Xem danh sách các Đợt song song (Waves) và phân công Model:**
   ```bash
   python scripts/orchestrate.py list
   ```
2. **Tạo sẵn 2 thư mục Worktree cô lập cho Claude và Gemini ở Đợt 1 (`WAVE-1`):**
   ```bash
   python scripts/orchestrate.py prepare WAVE-1
   ```
3. **In câu lệnh (Prompt) chuẩn xác để copy-paste cho Claude hoặc Gemini:**
   ```bash
   python scripts/orchestrate.py prompt WAVE-1 --agent claude
   python scripts/orchestrate.py prompt WAVE-1 --agent gemini
   ```
4. **Kiểm toán nhánh của Claude hoặc Gemini trước khi Merge (chặn đứng nếu sửa nhầm file của luồng kia + chạy CI):**
   ```bash
   python scripts/orchestrate.py audit WAVE-1 --agent gemini --worktree ../exam-wt/gemini-w1
   python scripts/orchestrate.py audit WAVE-1 --agent claude --worktree ../exam-wt/claude-w1
   ```
