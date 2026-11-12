# Kế hoạch Thiết kế & Triển khai ULP V11.3

> Duyệt ngày 12/11/2026. Nguồn chuẩn: `platform_manifest.yaml` (v11.3.0) và `chapters/`. Mỗi chặng dừng chờ review trước khi sang chặng kế.

## Context

Repo `exam-system` (FastAPI + SQLAlchemy 2.0 + Alembic, 23 bảng cũ, giao diện Jinja2) đang được nâng cấp lên ULP V11.3: 47 module, 17 ADR, 7 DB role, 11 cổng `GATE-01..10`. Bộ tài liệu vừa được thay bằng bản hợp nhất (`TL_Handover_V11_3_Engineering_Hardening_Unified.md` + `chapters/`), bổ sung nhóm Hardening `H-01..H-07` và thứ tự `Wave 0 → Wave 1 → Wave 2`. Kế hoạch này thay cho kế hoạch cũ (`tender-enchanting-adleman.md`): giữ các quyết định đã chốt, thêm Wave 0 lên trước Sprint 1.
## 1. Hiện trạng so với V11.3

| Hạng mục | Đã có | Còn thiếu |
|---|---|---|
| M0.1 CI & biên giới | `.importlinter`, `tests/test_architecture.py`, `app/modules/`, `app/contracts/` (rỗng), CI chạy ruff + lint-imports + gate ids + alembic check + pytest | `mypy --strict`, license/CVE audit (Cổng 6), IDOR suite (Cổng 4), golden (Cổng 5) |
| M0.2 Nền DB | `alembic/versions/c1d2e3f4a5b6_v113_foundation.py`: 5 extension, 7 role `NOBYPASSRLS`, `uuid_generate_v7()`, `current_app_tenant_id()`, `system_public_tenant_id()`, `app_system_job()`, `f_unaccent`, `tenants`, `fts_dictionary_versions`; `app/platform/rls.py` (`strict_tenant_rls`, `hybrid_write_rls`), `app/platform/tenancy.py` (`tenant_transaction`, `system_job_transaction`); `tests/test_db_foundation.py` (18 test) | 124/126 bảng Chương 4, `hybrid_read_policy` (21 bảng), policy grading/scheduler, trigger bất biến, GRANT theo bảng |
| M0.3 Hạ tầng dev | PgBouncer, Redis, Vault dev, ClamAV trong `docker-compose.yml`; login `ulp_api` (`app/platform/runtime_login.py`); `tests/test_runtime_role.py` | Patroni (hoãn), Vault Transit client, ClamAV client |
| M0.4 PDF engine | — | `pymupdf` còn trong `requirements.txt` và 23 file (13 file `app/`, 9 test, `universal_ingestor.py`) |
| H-02 SymPy | Chưa có dòng `sympy` nào trong repo | Toàn bộ |
| H-04 Autosave | `app/attempts.py::save_answers` + `PATCH /api/attempts/{id}/answers` (gộp thay đổi, không idempotency/version/hash); `app/static/js/attempt.js` | Contract mới, 4 bảng phòng thi, thư mục `frontend/` chưa tồn tại |
| Git | M0.1–M0.3 nằm trên 3 nhánh xếp chồng, **chưa vào `main`**; working tree có thay đổi tài liệu chưa commit | Merge + commit tài liệu |

Sai lệch đã phát hiện (sẽ sửa ở Bước 0, không tự sửa nội dung đặc tả):
- `ci/check_gate_ids.sh` bị ghi đè bằng bản nguyên văn tài liệu → **khôi phục bản M0.1** (đã chốt).
- `CLAUDE.md` mục 5 đang chứa nguyên văn lời nhắc thay vì 3 quy tắc; còn trỏ `frontend/src/lib/...` và `backend/...` trong khi bố cục đã chốt là `app/modules/<tên>/`.
- Docstring migration `c1d2e3f4a5b6` trỏ `handover-v11.3/02-...` (đã xóa) — chỉ là chú thích, không sửa migration đã phát hành.
- Chương 00 và 9.3B ghi manifest "11.2.0", tiêu đề Phần I ghi "10 ADRs"; file thật là 11.3.0 / 17 ADR.
- Chương 2 ghi "giữ nguyên xác thực JWT" nhưng repo dùng cookie phiên (`SessionMiddleware`), chưa có JWT.
- `verify_rls_hardening.sql`: Test 3 không khẳng định gì (`PERFORM count(*)`), Test 4 thiếu ca dương, cần dữ liệu seed.

## 2. Quyết định đã chốt

Giữ từ kế hoạch cũ: thứ tự module theo manifest · MVP Toán & Vật lí THPT · backfill nội dung, bỏ user/lượt thi cũ, không dual-write · `/api/v1` + test trước, Jinja giữ nguyên, Next.js bắt đầu từ Workspace 2 ở Sprint 5 · docker-compose gọn, Patroni/`GATE-05`/`GATE-10` hoãn đến hardening · bố cục `app/modules/<tên>/{public.py,router.py,internal/}` + `app/contracts/` · mỗi module một nhánh `feat/<module>-...`, commit tiếng Anh, không Co-Authored-By.

Chốt hôm nay:
1. **DDL Wave 0 = dry-run trên DB tạm**; DB ứng dụng vẫn nhận bảng theo từng module.
2. **Bảng cũ trùng tên → đổi thành `legacy_*`** (`users` ở F04, `questions` ở C01) bằng migration Expand.
3. **SymPy fail-closed ngoài Linux**: Windows trả `SANDBOX_WORKER_UNAVAILABLE`; chạy thật trong container/CI.
4. **`check_gate_ids.sh` dùng bản M0.1.**

## 3. Quy trình mỗi chặng (Review Gate)

Đề xuất (bảng, API, DoD, câu hỏi) → code trên nhánh riêng → `ruff check .` · `lint-imports` · `bash ci/check_gate_ids.sh` · `alembic upgrade head && alembic check` · `pytest` → tóm tắt file tạo/sửa + kết quả test → **dừng chờ duyệt**. Gặp điểm chưa rõ hoặc xung đột code ↔ đặc tả: dừng hỏi kèm phương án. Mỗi báo cáo có mục "💡 Đề xuất cải thiện".

Quy ước mọi bảng mới: PK UUIDv7, `tenant_id`, composite FK `(id, tenant_id)`, `ENABLE` + `FORCE RLS` qua builder trong `app/platform/rls.py`, GRANT tường minh theo bảng, test chạy bằng role runtime.

## 4. Lộ trình

### Bước 0 — Dọn nền (không đụng code ứng dụng)
- Sửa: `CLAUDE.md` (thay mục 5 bằng 3 quy tắc: Zero Blind Assumptions, Step-by-Step Review Gate, Proactive Improvements; ghi chú bố cục `app/modules`), `ci/check_gate_ids.sh` (khôi phục từ `HEAD`).
- Tạo: `docs/architecture/IMPLEMENTATION_PLAN_V11_3.md`.
- Giữ: `chapters/`, file tổng, manifest, `tests/security/`, `tests/load/`.
- Nghiệm thu: `bash ci/check_gate_ids.sh` in "Gate ids OK: 11 declared"; `ruff`, `lint-imports`, `pytest` xanh.
- Cần bạn quyết khi tới: commit tài liệu vào nhánh nào, merge M0.1–M0.3 vào `main`, số phận `toan11_de_goc.docx`.

### Wave 0 — đóng H-01..H-04 trước Sprint 1

| Chặng | Giữ / nâng cấp | Tạo mới | Test nghiệm thu |
|---|---|---|---|
| **W0.1 — H-01 + H-03: dry-run DDL Chương 4** | `chapters/04_postgresql_ddl_rls_grants.sql` (chỉ đọc; lỗi phát hiện được báo bạn, không tự sửa), `tests/security/verify_rls_hardening.sql`, `tests/conftest.py` | `tests/security/test_ddl_dry_run.py` (tạo DB tạm, áp file .sql, seed 2 tenant + system), `tests/security/seed_rls_fixture.sql`, `tests/security/grants_manifest.csv` (sinh từ mục 5 của DDL), bước CI "Gate 2b" | 100% bảng có tenant bật ENABLE+FORCE; 0 role `rolbypassrls`; 21 bảng hybrid: tenant khác không thấy `DRAFT`/`TAKEN_DOWN` của hệ thống, bảng con không lọt qua khi cha chưa `PUBLISHED AND is_immutable`; `knowledge_edges` kiểm cả hai đầu `is_active`; `ulp_exam_session_rw`/`ulp_worker_ingestion` nhận `42501` trên 2 bảng đáp án; `ulp_worker_grading` đọc được đáp án đề `SYSTEM_PUBLIC` (có khẳng định số dòng); scheduler thấy 0 dòng khi chưa đặt `app.system_job` và thấy đúng khi đặt; `role_table_grants` khớp `grants_manifest.csv`; `verify_rls_hardening.sql` chạy sạch |
| **W0.2 — H-01: builder policy dùng lại** | `app/platform/rls.py` (chỉ thêm, không đổi builder cũ) | `hybrid_read_root()`, `hybrid_read_child()`, `grading_read()`, `system_job_policy()`; mở rộng `tests/test_db_foundation.py` | Bảng thăm dò cha/con: con ẩn khi cha `DRAFT`; mọi policy sinh ra chứa `(SELECT current_app_tenant_id())`; test đối chiếu policy do builder sinh với policy trong DB dry-run |
| **W0.3 — H-02: SymPy sandbox** | — | `app/modules/quality/{public.py, internal/sympy_runner.py, internal/sympy_sandbox_worker.py}`, contract `.importlinter`, `sympy` vào `requirements.txt`, `tests/test_sympy_sandbox.py` | 12 ca RCE (`__import__`, `open`, `exec`, `__class__`, `lambda`, `;`…) bị chặn; `10**10**10` bị cắt bởi rlimit/timeout; thiếu file worker → `SANDBOX_WORKER_UNAVAILABLE`; Windows → `SANDBOX_WORKER_UNAVAILABLE`; test tĩnh: không có `sympify(` hay `import sympy` nào ngoài file worker; lệnh gọi đúng `python -I` |
| **W0.4 — H-04: contract + lõi autosave** | `app/attempts.py`, route `PATCH` cũ giữ nguyên đến C06 | `app/contracts/autosave.py` (`AutosaveRequestContract`, `AutosaveResponseContract`), `app/contracts/enums.py` (5 trục trạng thái), `app/modules/attempts/internal/autosave.py::process_autosave_idempotent_first` (hàm thuần, `expected_response_version: int`), `canonical_json` + `payload_hash`, `tests/test_autosave_contract.py` | 6 tình huống: replay cùng key → `IDEMPOTENT_REPLAY`; hash lệch → `422 PAYLOAD_HASH_MISMATCH`; đã nộp → `409 ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED`; quá hạn → `403 ATTEMPT_DEADLINE_EXPIRED`; lệch version → `409 OPTIMISTIC_VERSION_CONFLICT`; sequence cũ → `409 STALE_CLIENT_SEQUENCE`; thiếu `expected_response_version` → Pydantic từ chối; hash khớp `json.dumps(sort_keys=True, separators=(",",":"))` |
| **W0.5 — M0.4: PDF engine (ADR-010)** | 13 file `app/` dùng `fitz` (`app/ai/figures.py`, `app/ai/local_text.py`, `app/application/exam_pdf.py`, `pdf_preview.py`…), 9 file test | `app/infrastructure/pdf_engine.py` (`PDFEngineAdapter`: pypdfium2 + pdfplumber), gỡ `pymupdf` | `tests/test_figures.py`, `test_figure_bands.py`, `test_local_text.py`, `test_library_split.py` xanh; test tĩnh: 0 import `fitz`/`pymupdf`. Chặng lớn — sẽ đề xuất chia 2–3 PR |
| **W0.6 — Audit & kịch bản backfill** | `app/models.py` | `docs/architecture/BACKFILL_MAP.md` (bảng cũ → bảng mới, cột → cột) | Bạn duyệt trước Sprint 1 |

Phần Dexie của H-04 (`exam-autosave-store.ts`) làm cùng C06 ở Sprint 5 vì chưa có `frontend/`; `H-05..H-07` thuộc Wave 1–2.

### Track 1 — Core MVP (Sprint 1–6), mỗi dòng một chặng review

| Sprint | Module → `app/modules/<tên>` | Code cũ được nâng cấp | Bảng mới (Chương 4) | Test nghiệm thu (DoD) |
|---|---|---|---|---|
| 1 | G03 `outbox` | `app/worker.py` | `outbox_events`, `inbox_events` | Cùng `event_id` 5 lần → 1 tác dụng phụ; relay chạy dưới `ulp_system_scheduler` |
| 1 | G01 `config` | `app/config.py` | `platform_settings`, `feature_flags` | Đổi cờ có hiệu lực < 5s |
| 1 | F05 `access` | `app/security.py`, `app/deps.py` | `roles`, `permissions`, `role_permissions` | IDOR chéo tenant: 0 ca lọt |
| 1 | F04 `identity` | `app/routers/auth.py`; `users` → `legacy_users` | `users`, `memberships`, `parental_consents`, `user_pii_encryption_keys`, `erasure_certificates` | Dưới 16 tuổi thiếu consent bị chặn; xóa khóa Vault → không giải mã được |
| 1 | B03 `assets` | `app/infrastructure/storage.py` | `assets`, `content_assets`, `question_assets` | FREE chỉ lấy `PREVIEW_PDF`; chéo tenant → 404 |
| 1 | A01 `ingestion` | `app/ai/pipeline.py`, `ImportJob` | `sources`, `source_documents`, `document_versions`, `acquisition_jobs`, `pipeline_stage_runs`, `dead_letter_items` | Trùng sha256 trả bản cũ; retry idempotent 50 kịch bản |
| 2 | B04 `taxonomy` | `app/application/categories.py`, `curriculum_data.py` | `taxonomy_nodes`, `taxonomy_node_versions`, `taxonomy_aliases` | `<@` < 5ms; nâng version không đứt liên kết |
| 2 | B06 `curriculum` | `app/domain/k12_curriculum_*.py`, `sections_data.py` | `curriculum_versions`, `curriculum_nodes`, `taxonomy_mappings` | Seed KNTT Toán & Lý 10–12; CoverageReport < 200ms |
| 2 | B01 `content` | `app/application/books.py`, `library_publish.py` | `content_items`, `content_versions`, `publications`, `access_grants` | UPDATE bản `PUBLISHED` bị trigger chặn |
| 2 | B02 `authoring` | `app/routers/maker.py` | `content_drafts`, `content_blocks`, `comment_threads` | Bắt 100% lỗi KaTeX / asset tenant khác |
| 2 | E01 `ai_gateway` | `app/ai/key_rotator.py`, `provider.py`, `frugal.py`, `cache.py` | `ai_providers`, `ai_models`, `prompt_versions`, `ai_jobs`, `ai_traces` | 429 → xoay key < 50ms; chạm trần ngân sách chặn 100% |
| 2 | G02 `audit` | — | `audit_events` (phân vùng), `takedown_cases` | Takedown < 30s; UPDATE/DELETE audit bị chặn |
| 3 | A05 `parsers` | `app/ai/universal_ingestor.py`, `extract_engine.py` | `parser_definitions`, `extraction_runs` | 20 LibreOffice song song, 0 tiến trình mồ côi |
| 3 | A06 `layout` | `app/ai/figures.py`, `local_text.py` | `extracted_pages`, `layout_regions` | Không rớt `-OH`/`v(m/s)`; RAM < 250MB / 100 trang |
| 3 | A07 `extraction` | `app/ai/extract_rules.py`, `normalize.py`, `pipeline/domain/exam_split.py` | `content_candidates`, `question_candidates` | Bayes: 1 Content + 67 Question |
| 3 | A09 `review` | `app/ai/background_audit_jobs.py` | `review_tasks`, `review_decisions`, `source_rights` | Publish khi `PENDING_REVIEW` → `CheckViolation` |
| 3 | C02 `quality` (dùng W0.3) | `app/ai/check.py`, `pipeline/use_cases/rescue.py` | `verification_runs` | Phát hiện 100% đáp án mâu thuẫn SymPy |
| 4 | C01 `questions` | `app/models.py::Question` → `legacy_questions`, `question_import.py`, `question_rules.py` | `questions`, `question_versions`, `question_version_answers`, `question_options`, `question_stimuli` | 6 loại câu; sửa bản Publish sinh `version_no + 1`; role phòng thi bị chặn đáp án |
| 4 | A08 `dedup` | `app/domain/question_hash.py`, `application/similar.py` | `duplicate_clusters`, `duplicate_members` | `H2SO4` ≠ `H2SO3`; `10g` ≡ `20g` |
| 4 | A04 `exam_import` | `app/application/exam_pdf.py`, `answer_keys.py` | `exam_import_sessions`, `provenance_records` | Precision ≥ 98%, Recall ≥ 96% trên Golden |
| 4 | B07 `search` | `app/routers/library.py` | `search_documents`, `collections`, `collection_items` | Tìm không dấu p95 < 120ms |
| 4 | C03 `frameworks` | `app/application/matrix_import.py` | `assessment_frameworks`, `framework_versions`, `blueprints`, `scoring_scheme_versions` | 22 câu Toán / 28 câu Lý; ShortageReport đúng |
| 5 | C04 `assembly` | `app/application/custom_quiz.py`, `paper.py` | `assessments`, `assessment_versions`, `assessment_items`, `assessment_answer_keys` | Cùng seed cùng đề; snapshot 0% đáp án; < 500ms |
| 5 | C05 `delivery` | `app/attempts.py::start_or_resume` | `attempts` | F5/đổi máy giữ nguyên thứ tự; JSON không lộ đáp án |
| 5 | C06 `attempts` (dùng W0.4) + `frontend/` Workspace 2 | `save_answers`, `app/static/js/attempt.js` | `responses`, `response_revisions`, `autosave_idempotency_registry`; `frontend/src/lib/exam-autosave-store.ts` (Dexie) | Chaos: replay 200, gói cũ 409, sập Redis vẫn lưu; `tsc --noEmit` + `fake-indexeddb` |
| 6 | C07 `grading` + đóng gate | `app/attempts.py::grade`, `application/essay_grading.py` | `grades`, `grading_details`, `rubric_versions`, `appeals` | 100% tổ hợp điểm Phần I/II/III; Regrade giữ `revision_no`; đóng `GATE-01, 02, 03, 04, 06a, 07` |

### Track 2 — Intelligent & LMS (Sprint 7–9)
- **S7:** B05 `knowledge_graph` (chặn chu trình `422`), D01 `dashboard`, D02 `learning_path`, D03 `practice`, D05 `error_notebook` (đúng 2 câu liên tiếp mới `RESOLVED`). Nâng cấp `app/application/report.py`, `knowledge_linker.py`, `routers/student.py`.
- **S8:** F01 `classes`, F02 `assignments`, F03 `gradebook`, F06 `notifications` (phát lại 3 lần → 1 thông báo) + `GATE-06b`. Nâng cấp `app/application/teacher_tests.py`, `routers/teacher.py`.
- **S9:** D04 `flashcards` (FSRS-5 khớp vector `py-fsrs`), E02 `tutor` (Exam Lock `403`), E04 `explanations` (SymPy ≥ 95%) + `GATE-08`.

### Track 3 — Expansion & Hardening (Sprint 10–14)
- **S10:** A02 `crawler` (nâng cấp `app/pipeline/crawl/*`, `parsers/*`; thay Kafka/Mongo bằng outbox + Bronze; bộ test SSRF), A03 `drive_sync` (nâng cấp `pipeline/use_cases/drive_sync.py`; Zip Bomb), E03 `generation`.
- **S11:** D06 `mastery` (IRT 4 bậc theo manifest), E05 `recommendations`, E06 `teacher_copilot`.
- **S12:** G04 `analytics`, G05 `billing` (nâng cấp `app/plans.py`), G06 `vertical_packs`.
- **S13–14:** Patroni 3 node, k6 5.000 CCU (`GATE-05`), DR drill (`GATE-10`), SBOM/pentest/DPIA (`GATE-09`).

## 5. Câu hỏi còn mở (hỏi lại đúng chặng, không chặn Bước 0 và W0.1)

1. **Git (Bước 0):** M0.1–M0.3 đã có PR chưa? Tôi mở PR hay bạn tự merge; tài liệu mới commit vào nhánh nào?
2. **Golden Corpus (trước Sprint 3):** bạn có 11 đề Golden + 30 đề Held-out đã ẩn danh chưa, lưu ở đâu? `toan11_de_goc.docx` ở gốc repo có phải một đề trong số đó (không được vào Git)?
3. **Xác thực (F05, Sprint 1):** `/api/v1` dùng JWT mới (cookie phiên vẫn phục vụ Jinja) hay tiếp tục cookie phiên cho cả hai?
4. **F04:** ngưỡng tuổi và hình thức xác nhận phụ huynh (OTP email / hợp đồng trường); Vault dev-mode đủ cho giai đoạn này?
5. **E01:** nhà cung cấp LLM chính/dự phòng và trần `daily_ai_budget_usd` mặc định.
6. **Phương án A (30 tuần, 47 module) hay B (hoãn A02, E03, G06):** chốt cuối Wave 0.
7. **Ngoài phạm vi code:** DPIA/DPA, pentest độc lập, ký duyệt pháp chế — ai phụ trách?

## 6. 💡 Đề xuất cải thiện để bạn cân nhắc

| # | Đề xuất | Ưu | Nhược |
|---|---|---|---|
| 1 | Viết lại `verify_rls_hardening.sql` thành pytest có seed và khẳng định thật (giữ file .sql làm bản tham chiếu) | Test 3/4 hiện không bắt được lỗi; pytest báo lỗi rõ theo từng ca | Hai nơi cùng mô tả một bộ kiểm tra |
| 2 | Test "policy coverage": mọi bảng có `tenant_id` phải thuộc đúng một nhóm (strict / hybrid / global) khai báo trong `app/platform/rls.py` | Quên policy cho bảng mới là CI đỏ ngay, không đợi GATE-01 | Thêm một danh sách phải cập nhật mỗi module |
| 3 | Sandbox SymPy: ngoài whitelist chuỗi, duyệt AST (`ast.parse`) chặn `Attribute`, `Subscript`, lũy thừa lồng nhau và số mũ lớn | `10**10**10` bị từ chối trước khi tốn CPU; không phụ thuộc hoàn toàn vào rlimit | Lệch nhẹ so với mã mẫu Mục 6.3 (chặt hơn, không lỏng hơn) |
| 4 | `payload_hash`: dùng một bộ vector thử chung (JSON file) cho cả Python và TypeScript | Chặn lệch canonical JSON giữa server và Dexie (số thực, Unicode, thứ tự khóa) | Thêm fixture phải giữ đồng bộ |
| 5 | Thêm Cổng 6 (pip-licenses + pip-audit) ngay ở W0.5 | AGPL không quay lại sau khi gỡ PyMuPDF | CI chậm thêm ~30–60s; có thể phải lập allowlist |
| 6 | `autosave_idempotency_registry` + `response_revisions`: phân vùng theo tháng ngay từ đầu, dọn bằng DROP PARTITION | Tránh bloat ở 5.000 CCU, không cần DELETE hàng loạt | DDL phức tạp hơn; cần job tạo partition (đã có `ensure_monthly_partitions`) |
| 7 | Tạo `legacy_*` bằng đổi tên + VIEW cùng tên cũ trong 1 sprint chuyển tiếp | Script/SQL cũ ngoài ORM không vỡ | VIEW trùng tên bảng V11.3 nên chỉ dùng được cho bảng không va chạm; thêm một bước Contract |

## 7. Verification

- Mỗi chặng: `ruff check .` · `lint-imports` · `bash ci/check_gate_ids.sh` · `alembic upgrade head && alembic check` · `pytest -q` + test DoD riêng của chặng.
- Wave 0: `pytest tests/security -q` (dry-run DDL trên PostgreSQL 17 của docker-compose/CI); `docker compose run --rm app pytest tests/test_sympy_sandbox.py` (Linux, có rlimit); `pytest tests/test_autosave_contract.py`.
- Mỗi sprint: FORCE RLS trên mọi bảng mới, IDOR chéo tenant, hai role bị chặn đọc đáp án; demo localhost qua `docker compose up` trước khi merge.

## 8. Bước đầu tiên sau khi duyệt

Bước 0 (ghi 3 quy tắc vào `CLAUDE.md`, khôi phục `ci/check_gate_ids.sh`, lưu kế hoạch vào `docs/architecture/IMPLEMENTATION_PLAN_V11_3.md`, chạy bộ kiểm tra) → báo cáo → dừng chờ duyệt trước W0.1.
