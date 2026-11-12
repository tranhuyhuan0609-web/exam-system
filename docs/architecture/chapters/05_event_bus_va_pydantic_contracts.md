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

