# Bản đồ backfill: bảng cũ → bảng V11.3 (W0.6)

Trạng thái: **bản đề xuất, chờ duyệt trước Sprint 1.** Số liệu đo trên database dev ngày 13/11/2026.

Nguyên tắc đã chốt: backfill **nội dung** (câu hỏi, đáp án, thư viện, danh mục, đề); **bỏ** user và lượt thi cũ;
không dual-write. Bảng cũ trùng tên được đổi thành `legacy_*` khi module tới lượt. Mỗi đợt backfill chạy theo lô 500 dòng,
ghi `legacy_id` vào cột truy vết để chạy lại được (idempotent) và đối soát bằng số dòng + `content_hash`.

## 1. Quy mô dữ liệu hiện có

| Bảng cũ | Số dòng | Ghi chú |
|---|---:|---|
| `questions` | 17.217 | 16.932 trắc nghiệm, 190 tự luận, 68 trả lời ngắn, 27 đúng/sai; 800 có hình; 6.124 có embedding |
| `crawled_questions` | 20.514 | 16.851 đã thành câu hỏi; 2.074 chờ duyệt; 909 trùng; 546 chờ AI |
| `categories` | 2.871 | cây 4 mức (depth 0–3), gốc là "môn + lớp" |
| `knowledge_units` | 80 | CHAPTER / LESSON / PROBLEM_TYPE |
| `library_documents` | 642 | 440 đã xuất bản, 154 chờ duyệt, 48 ẩn |
| `exams` | 44 | 33 đã xuất bản |
| `answer_keys` | 24 | |
| `knowledge_articles` | 1 | |
| `users` / `exam_attempts` | 3 / 36 | bỏ, không backfill |

Nguồn câu hỏi: 16.851 từ crawler (loigiaihay 11.364, tuyensinh247 3.739, hoc247 2.823, vietjack_english 1.295,
opentrivia 750, vietjack 543 — tính trên `crawled_questions`), 345 từ nhập file bằng AI, 21 nhập tay.

## 2. Ánh xạ theo module

| Module (Sprint) | Bảng cũ | Bảng V11.3 | Quy tắc chuyển |
|---|---|---|---|
| F04 (S1) | `users` | `users`, `memberships` | Không backfill. Đổi tên thành `legacy_users`; tạo lại 1 tài khoản admin ở tenant hệ thống. Giao diện Jinja tiếp tục đăng nhập bằng `legacy_users` cho tới khi workspace mới thay thế |
| B03 (S1) | `questions.image_path`, `crawled_questions.images`, `library_documents.storage_key` / `preview_storage_key` | `assets` (+ `question_assets`, `content_assets` ở module sau) | Mỗi file một dòng `assets`: `asset_role = RAW_SOURCE` cho file gốc thư viện, `FIGURE_CROP` cho hình câu hỏi, `PREVIEW_PDF` cho bản xem trước; tính lại `sha256` từ object thật |
| A01 (S1) | `crawler_sites` | `sources` | Mỗi site một `sources`, `rights_status = PENDING_REVIEW`. `import_jobs`, `crawl_runs` là lịch sử vận hành: không backfill |
| A01 (S1) | `library_documents` (file gốc) | `source_documents`, `document_versions` | `checksum` → `checksum_sha256` (tính lại nếu đang là md5 của Drive); `source` + `source_file_id` → định danh nguồn |
| B04 (S2) | `categories` (depth ≥ 1), `knowledge_units` loại PROBLEM_TYPE | `taxonomy_nodes`, `taxonomy_node_versions` | `code_path` kiểu `ltree` dựng từ mã môn + slug hóa tên; `categories.id` cũ lưu ở `taxonomy_aliases` để tra ngược |
| B06 (S2) | `categories` (gốc "môn + lớp"), `knowledge_units` CHAPTER / LESSON, `app/domain/k12_curriculum_data.py` | `curriculum_versions`, `curriculum_nodes`, `taxonomy_mappings` | Một `curriculum_versions` cho mỗi bộ sách + môn + lớp; cây 4 cấp CHAPTER → LESSON → SECTION → PROBLEM_TYPE |
| B01 (S2) | `library_documents` (bản đọc), `knowledge_articles` | `content_items`, `content_versions` | `kind` → `content_type` (BOOK/TOPIC/PAPER → `DOCUMENT`, bài kiến thức → `ARTICLE`); `content_markdown` và các trường `*_md` → `blocks_json`; `status` cũ → 5 trục (xem mục 3) |
| C01 (S4) | `questions` | `questions`, `question_versions`, `question_options`, `question_version_answers` | Đổi tên bảng cũ thành `legacy_questions`. Mỗi câu → 1 `questions` + 1 `question_versions` (`version_no = 1`). `content` → `stem_json`; `choices` (bỏ cờ đúng/sai) → `question_options`; cờ đúng/sai + `accepted_answers` + `explanation` → `question_version_answers`. `stable_code = 'LEG-' || id` |
| C01 (S4) | `question_categories`, `question_target_tags` | `questions.primary_taxonomy_node_id`, `exam_framework_tags` | Qua bảng tra `taxonomy_aliases` |
| A08 (S4) | `questions.content_hash`, `embedding`; `crawled_questions` trạng thái DUPLICATE / SIMILAR | `question_versions.content_hash`, `structural_hash`, `duplicate_clusters`, `duplicate_members` | `content_hash` tính lại sau khi chuẩn hóa NFC (hash cũ không dùng lại); `structural_hash` tính mới; embedding chép nguyên |
| A07 / A09 (S3) | `crawled_questions` chưa thành câu hỏi (2.074 + 546) | `question_candidates`, `review_tasks` | Chỉ chuyển nhóm NEEDS_REVIEW; nhóm PENDING_AI chạy lại qua pipeline mới; DUPLICATE / REJECTED bỏ |
| A04 (S4) | `questions.source_url`, `crawled_questions.doc_id` / `source_url` | `provenance_records` | `acquisition_method = WEB_CRAWL` với `source_url`; `raw_html_sha256` lấy từ Bronze (Mongo) nếu còn |
| C03 (S4) | `exam_targets`, `target_curriculum_mappings`, `exams.matrix` | `assessment_frameworks`, `framework_versions`, `blueprints` | Seed lại theo cấu trúc THPTQG 2025+ của đặc tả; ma trận cũ chỉ dùng tham khảo |
| C04 (S5) | `exams`, `exam_questions`, `answer_keys` | `assessments`, `assessment_versions`, `assessment_items`, `assessment_answer_keys` | Mỗi đề → 1 `assessments` + 1 phiên bản; `structure_snapshot_json` dựng từ `exam_questions` (không chứa đáp án); đáp án vào `assessment_answer_keys` |
| C05–C07 (S5–6) | `exam_attempts`, `exam_enrollments` | — | Bỏ (đã chốt) |
| E01 (S2) | `ai_usage`, `ai_cache` | `ai_traces` | Không backfill: thiếu `prompt_version_id`, `input_sha256`. `ai_cache` giữ nguyên làm cache của pipeline cũ |
| G05 (S12) | `users.pro_until`, `library_views` | — | Bỏ cùng với user |

## 3. Ánh xạ giá trị

**Loại câu hỏi:** `MULTIPLE_CHOICE` → `SINGLE_CHOICE` · `TRUE_FALSE` → `CLUSTER_TRUE_FALSE` · `SHORT_ANSWER` → `SHORT_NUMERIC` · `ESSAY` → `ESSAY`.

**Độ khó:** `RECOGNITION` → `NB` · `UNDERSTANDING` → `TH` · `APPLICATION` → `VD` · `ADVANCED_APPLICATION` → `VDC` · `HSG` → `VDC` kèm thẻ `HSG` trong `exam_framework_tags`.

**Năm trục trạng thái cho câu hỏi cũ:**

| Trục | Giá trị gán | Căn cứ |
|---|---|---|
| `acquisition_status` | `NORMALIZED` | Đã qua pipeline cũ |
| `verification_status` | `VERIFIED` khi `audit_status` là `VERIFIED_CONSENSUS` hoặc `VERIFIED_BY_HUMAN` (9 câu); `AUTO_VERIFIED` khi `is_verified` và có `answer_source`; còn lại `UNCHECKED` | Chỉ 9 câu có bằng chứng kiểm chứng thật |
| `rights_status` | `OWNED` cho 155 câu có `origin_type = AUTHORED`; `PENDING_REVIEW` cho 17.062 câu `PUBLIC` còn lại (16.851 crawler, 190 nhập file, 21 nhập tay) | Đặc tả bắt buộc fail-closed |
| `publication_status` | `DRAFT` cho mọi câu chưa đủ điều kiện; `PUBLISHED` chỉ khi `VERIFIED` và quyền hợp lệ | Cổng xuất bản của DB sẽ từ chối các trường hợp khác |
| `visibility_scope` | `SYSTEM_PUBLIC` (tenant hệ thống) | Kho chung |

## 4. Quyết định đã chốt (13/11/2026)

- **Phạm vi Sprint 4:** chỉ backfill Toán và Vật lí lớp 10–12 trước; các môn và khối khác chuyển theo từng đợt mở rộng.
- **Độ khó `HSG`:** gán `VDC` và thêm thẻ `HSG` vào `exam_framework_tags`.

## 5. Hệ quả còn chờ quyết trước khi backfill

1. **98% kho câu hỏi sẽ không xuất bản được ngay.** 16.851 câu đến từ crawler nên vào `rights_status = PENDING_REVIEW`; cộng với
   việc chỉ 9 câu ở mức `VERIFIED`, sau backfill số câu `PUBLISHED` gần như bằng 0 cho tới khi A09 (duyệt quyền) và C02
   (kiểm chứng) xử lý xong. Bạn muốn: (a) chấp nhận, duyệt quyền theo từng nguồn rồi mở dần; (b) bạn xác nhận quyền theo
   từng site ngay bây giờ (ví dụ site nào được phép dùng phái sinh) để backfill gán luôn; hay (c) giữ kho cũ phục vụ qua
   giao diện Jinja song song cho tới khi kho mới đủ lớn?
2. **`verification_status` cho câu chỉ có `is_verified = true`**: cờ này mặc định là `true` ở schema cũ nên không phải bằng
   chứng. Theo quy tắc ở mục 3, 17.027 câu có `answer_source` sẽ thành `AUTO_VERIFIED` và phần còn lại `UNCHECKED`. Cả hai
   mức đều chưa qua được cổng xuất bản (cần `VERIFIED`), nên C02 phải kiểm chứng lại hoặc người duyệt xác nhận theo lô.
3. **Thư viện 440 tài liệu đã xuất bản** phần lớn là đề HSG/đề thi lấy từ Drive và nguồn mở: cùng câu hỏi về quyền như mục 1.
4. **Hash cũ không dùng lại được** vì thuật toán V11.3 chuẩn hóa NFC và đổi cách băm; việc khử trùng phải chạy lại trên toàn
   kho (A08), có thể gộp thêm câu trùng mà hệ thống cũ bỏ sót.

## 6. Thứ tự và kiểm tra

1. Mỗi module tự mang script backfill của nó (`app/modules/<tên>/internal/backfill.py`), chạy bằng lệnh CLI, không chạy
   trong migration.
2. Thứ tự phụ thuộc: `assets` → `sources` / `document_versions` → `taxonomy` → `curriculum` → `content` → `questions` →
   `dedup` → `assessments`.
3. Đối soát sau mỗi đợt: số dòng nguồn = số dòng đích + số dòng bị loại (có lý do từng dòng); so `content_hash` mới của
   100 câu lấy mẫu với kết quả tính tay; chạy lại lần hai phải tạo 0 dòng mới.
4. Bảng `legacy_*` chỉ bị xóa ở bước Contract, sau khi giao diện mới thay giao diện Jinja tương ứng.
