from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"  # creates private scheduled tests while their Pro plan is active


class Difficulty(StrEnum):
    RECOGNITION = "RECOGNITION"  # Nhận biết
    UNDERSTANDING = "UNDERSTANDING"  # Thông hiểu
    APPLICATION = "APPLICATION"  # Vận dụng
    ADVANCED_APPLICATION = "ADVANCED_APPLICATION"  # Vận dụng cao


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"  # Phần I: one correct option
    TRUE_FALSE = "TRUE_FALSE"  # Phần II: four statements, each true or false
    SHORT_ANSWER = "SHORT_ANSWER"  # Phần III: type a short (numeric) answer
    ESSAY = "ESSAY"  # tự luận: free-form worked answer, graded by AI against the model solution


class SourceType(StrEnum):
    MANUAL = "MANUAL"  # typed in the admin form
    FILE_IMPORT = "FILE_IMPORT"  # Excel / Word / text template
    AI_UPLOAD = "AI_UPLOAD"  # extracted and solved by the AI pipeline
    CRAWLER = "CRAWLER"  # collected from the web (phase 4)


class QuestionSource(StrEnum):
    FIXED = "FIXED"  # the questions listed in exam_questions, in order
    MATRIX = "MATRIX"  # drawn at random per attempt from the bank following Exam.matrix


class ExamMode(StrEnum):
    PRACTICE = "PRACTICE"  # self-paced, start any time
    SCHEDULED = "SCHEDULED"  # fixed time window (phase 3)


class ExamVisibility(StrEnum):
    PUBLIC = "PUBLIC"  # listed on the site once published
    PERSONAL = "PERSONAL"  # a quiz a student built for themself; only its owner (created_by) sees it
    PRIVATE = "PRIVATE"  # a teacher's test: never listed, students join with the link + access code


EMBEDDING_DIM = 768


class CrawlRunStatus(StrEnum):
    QUEUED = "QUEUED"  # requested from the admin page; the scheduler starts it
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class CrawlItemStatus(StrEnum):
    """Silver: where a crawled question is in the pipeline."""

    PENDING_AI = "PENDING_AI"  # waiting for Gemini (formula images, plain-text formulas, missing solution)
    READY = "READY"  # clean; dedup + labelling decide what happens next
    PUBLISHED = "PUBLISHED"  # copied to questions (Gold)
    DUPLICATE = "DUPLICATE"  # exact match (SHA-256) with a question or another crawled item
    SIMILAR = "SIMILAR"  # semantic match (cosine >= threshold) with a question
    NEEDS_REVIEW = "NEEDS_REVIEW"  # no category, parser issues or AI failure: an admin decides
    REJECTED = "REJECTED"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    DONE = "DONE"  # drafts ready for review
    FAILED = "FAILED"


class GradingStatus(StrEnum):
    COMPLETE = "COMPLETE"  # every question graded
    PENDING_AI = "PENDING_AI"  # essays waiting for the AI grader (worker)
    FAILED = "FAILED"  # the AI grader gave up; essays count as 0 until regraded


class AttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    # Stored as VARCHAR + CHECK constraint so new values only need a constraint change, not a PG type migration.
    return SAEnum(enum_cls, name=name, native_enum=False, create_constraint=True, length=20)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Always stored lower-cased so uniqueness is case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), default=UserRole.STUDENT)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Teacher Pro plan: active while pro_until is in the future (set by an admin).
    pro_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pro_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),)

    def has_pro(self, now: datetime) -> bool:
        return self.role == UserRole.ADMIN or (
            self.role == UserRole.TEACHER and self.pro_until is not None and self.pro_until > now
        )


class Category(TimestampMixin, Base):
    """One node of the table of contents: a root is a subject (Vật lí), below it any number of levels
    (Lớp 11 → Chương 1 → Con lắc lò xo...).

    `path` lists the ids from the root down to this node ("/1/5/12/"), so a whole branch is selected with
    `path LIKE '/1/5/%'` and the root id is the first element.
    """

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str | None] = mapped_column(String(200), unique=True)  # roots only, for URLs
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(500), index=True, default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)  # 0 = root (subject)

    @property
    def root_id(self) -> int:
        return int(self.path.strip("/").split("/")[0])


QUESTION_MIN_CHOICES, QUESTION_MAX_CHOICES = 2, 6


class Question(TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The subject (root category) and the most specific category the question belongs to.
    root_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    difficulty: Mapped[Difficulty] = mapped_column(_enum(Difficulty, "difficulty"))
    question_type: Mapped[QuestionType] = mapped_column(
        _enum(QuestionType, "question_type"),
        default=QuestionType.MULTIPLE_CHOICE,
        server_default=QuestionType.MULTIPLE_CHOICE.value,
    )
    # Question text; may contain LaTeX ($...$ / $$...$$) rendered client-side.
    content: Mapped[str] = mapped_column(Text)
    # [{"id": "c1", "content": "...", "is_correct": true}, ...]
    # Choices keep stable ids so answers survive shuffling; is_correct must never be sent to students.
    # For TRUE_FALSE, each entry is a statement and is_correct means "this statement is true".
    choices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # SHORT_ANSWER only: accepted answers, compared after normalisation ("1.50" == "1,5").
    accepted_answers: Mapped[list[str] | None] = mapped_column(JSONB)
    # Points for ESSAY questions (other types use the THPT weights); NULL = default.
    points: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    explanation: Mapped[str | None] = mapped_column(Text)
    # Figure (graph, circuit...): storage key under figures/, publicly readable.
    image_path: Mapped[str | None] = mapped_column(String(300))
    source_type: Mapped[SourceType] = mapped_column(
        _enum(SourceType, "source_type"), default=SourceType.MANUAL, server_default=SourceType.MANUAL.value
    )
    # SHA-256 of the normalised type + text + options (app/domain/question_hash.py), for exact-duplicate checks.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Only verified questions can be put into exams; crawled questions start unverified.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Crawled questions: the page they came from (attribution).
    source_url: Mapped[str | None] = mapped_column(String(500))
    # Gemini embedding of the normalised text, for semantic duplicate detection (pgvector, cosine).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)

    __table_args__ = (
        Index(
            "ix_questions_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Exam(TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    exam_mode: Mapped[ExamMode] = mapped_column(_enum(ExamMode, "exam_mode"), default=ExamMode.PRACTICE)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int | None] = mapped_column(Integer)  # NULL = unlimited
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    question_source: Mapped[QuestionSource] = mapped_column(
        _enum(QuestionSource, "question_source"),
        default=QuestionSource.FIXED,
        server_default=QuestionSource.FIXED.value,
    )
    # MATRIX rules (see app/application/paper.py):
    # [{"scope": "c:3", "difficulty": "RECOGNITION" | null, "type": "MULTIPLE_CHOICE" | null, "count": 10}]
    matrix: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    shuffle_choices: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Students may pick which parts (multiple choice, true/false, short answer, essay) to practise.
    allow_custom_parts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    visibility: Mapped[ExamVisibility] = mapped_column(
        _enum(ExamVisibility, "exam_visibility"),
        default=ExamVisibility.PUBLIC,
        server_default=ExamVisibility.PUBLIC.value,
        index=True,
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # PRIVATE tests: the link carries access_token; students also type access_code.
    access_token: Mapped[str | None] = mapped_column(String(32), unique=True)
    access_code: Mapped[str | None] = mapped_column(String(12))

    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="ck_exams_duration_positive"),
        CheckConstraint("max_attempts IS NULL OR max_attempts > 0", name="ck_exams_max_attempts_positive"),
    )


class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="RESTRICT"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class ExamEnrollment(Base):
    """A student who opened a private test's link and entered its code."""

    __tablename__ = "exam_enrollments"

    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[AttemptStatus] = mapped_column(
        _enum(AttemptStatus, "attempt_status"), default=AttemptStatus.IN_PROGRESS
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # {"<question_id>": "<choice_id>"}
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Frozen copy of the paper (questions, choice order, correct ids) at start time,
    # so later edits to the bank never change an attempt's grading or review.
    paper_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    total_questions: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    grading_status: Mapped[GradingStatus] = mapped_column(
        _enum(GradingStatus, "grading_status"),
        default=GradingStatus.COMPLETE,
        server_default=GradingStatus.COMPLETE.value,
    )
    # {"<question_id>": {"points": "0.75", "max_points": "1", "feedback": "...", "solution": "..."}}
    essay_grades: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    grading_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Question ids the student flagged as "unsure" (🚩), shown in the submit dialog and the review.
    flags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # Seconds the student spent on each question {"<question_id>": 73}, measured in the browser.
    question_times: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("user_id", "exam_id", "attempt_no", name="uq_attempts_user_exam_no"),
        Index("ix_attempts_user_exam", "user_id", "exam_id"),
        # Lets the phase-2 worker find expired in-progress attempts cheaply.
        Index(
            "ix_attempts_in_progress_expires",
            "expires_at",
            postgresql_where="status = 'IN_PROGRESS'",
        ),
    )


class ImportJob(Base):
    """An uploaded exam file processed by the AI worker into draft questions awaiting review."""

    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    original_filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(300))  # relative to data_dir
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), default=JobStatus.QUEUED)
    stage: Mapped[str | None] = mapped_column(String(200))  # human-readable progress, e.g. "Đang giải câu 6–10"
    progress_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    progress_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Where published questions go.
    root_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    default_difficulty: Mapped[Difficulty] = mapped_column(_enum(Difficulty, "difficulty"))
    # Draft questions produced by the pipeline and edited during review (see app/ai/pipeline.py).
    drafts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    models_used: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_import_jobs_queued", "id", postgresql_where="status = 'QUEUED'"),)


# ---------------------------------------------------------------- crawler (app/pipeline)


class CrawlerSite(Base):
    """One crawled website; `config` mirrors configs/sites/<site_id>.yaml (`python -m app.pipeline sync-configs`)."""

    __tablename__ = "crawler_sites"

    site_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("crawler_sites.site_id", ondelete="CASCADE"), index=True)
    status: Mapped[CrawlRunStatus] = mapped_column(
        _enum(CrawlRunStatus, "crawl_run_status"), default=CrawlRunStatus.QUEUED
    )
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | schedule | cli
    max_pages: Mapped[int | None] = mapped_column(Integer)
    seed_filter: Mapped[str | None] = mapped_column(String(200))  # only seeds whose label contains this
    force: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # ignore refresh_days
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    docs_published: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawledQuestion(Base):
    """Silver layer: one question parsed from a Bronze page, cleaned, deduplicated and labelled."""

    __tablename__ = "crawled_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("crawler_sites.site_id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(300))
    doc_id: Mapped[str] = mapped_column(String(80))  # Bronze document (MongoDB raw_pages)
    source_url: Mapped[str] = mapped_column(String(500))
    source_title: Mapped[str] = mapped_column(String(500), default="")
    # Place in the source's table of contents (root, series, chapter, lesson), for auto_toc labelling.
    source_context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    question_type: Mapped[QuestionType] = mapped_column(_enum(QuestionType, "question_type"))
    content: Mapped[str] = mapped_column(Text)
    choices: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    accepted_answers: Mapped[list[str] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_by_ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ai_provider: Mapped[str | None] = mapped_column(String(30))  # which AI normalised it (gemini, groq...)
    image_path: Mapped[str | None] = mapped_column(String(300))  # re-hosted figure (our storage)
    images: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # [{"src", "key", "inline"}]
    difficulty: Mapped[Difficulty] = mapped_column(_enum(Difficulty, "difficulty"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    source_hash: Mapped[str] = mapped_column(String(64))  # parsed text: tells whether the source changed
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # final text, compared with questions
    status: Mapped[CrawlItemStatus] = mapped_column(_enum(CrawlItemStatus, "crawl_item_status"), index=True)
    issues: Mapped[list[str]] = mapped_column(JSONB, default=list)
    similarity: Mapped[float | None] = mapped_column(Numeric(5, 4))  # best cosine match found by dedup
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    ai_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("site_id", "external_id", name="uq_crawled_questions_site_external"),)


class CrawlCategoryMap(Base):
    """A source page (lesson) assigned to a category, by the labelling rules or by an admin (remembered)."""

    __tablename__ = "crawl_category_map"

    site_id: Mapped[str] = mapped_column(ForeignKey("crawler_sites.site_id", ondelete="CASCADE"), primary_key=True)
    source_url: Mapped[str] = mapped_column(String(500), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    source_title: Mapped[str] = mapped_column(String(500), default="")
    by_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class AIUsage(Base):
    """AI calls per provider and day made by the crawler (budgets in AI_DAILY_LIMITS)."""

    __tablename__ = "ai_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


# ---------------------------------------------------------------- library (books & documents, teacher Pro)


class LibraryKind(StrEnum):
    BOOK = "BOOK"  # sách tham khảo
    TOPIC = "TOPIC"  # chuyên đề, tài liệu ôn tập
    EXAM = "EXAM"  # đề thi, đề kiểm tra
    HSG = "HSG"  # đề / tài liệu học sinh giỏi
    OTHER = "OTHER"


class LibraryStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"  # imported, an admin checks the source and the rights first
    PUBLISHED = "PUBLISHED"  # visible to teachers with Pro
    HIDDEN = "HIDDEN"


class LibraryDocument(Base):
    """A file in the library: imported from a Google Drive folder (or uploaded), kept in the bronze-raw bucket."""

    __tablename__ = "library_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    kind: Mapped[LibraryKind] = mapped_column(_enum(LibraryKind, "library_kind"), default=LibraryKind.OTHER)
    status: Mapped[LibraryStatus] = mapped_column(
        _enum(LibraryStatus, "library_status"), default=LibraryStatus.PENDING_REVIEW, index=True
    )
    # Folder names from the source root down to the file's folder, e.g. ["10. HSG DUYÊN HẢI…", "Vật lí"].
    folder_path: Mapped[list[str]] = mapped_column(JSONB, default=list)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(50))  # e.g. "drive:thptqg2026", "upload"
    source_file_id: Mapped[str | None] = mapped_column(String(200), unique=True)  # Drive file id
    source_url: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(64))  # Drive md5 / our sha256: re-download only on change
    storage_key: Mapped[str] = mapped_column(String(300))  # in the bronze-raw bucket
    mime: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer, default=0)
    downloadable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    license_note: Mapped[str | None] = mapped_column(String(300))
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
