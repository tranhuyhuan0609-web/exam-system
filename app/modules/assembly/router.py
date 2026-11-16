"""FastAPI /api/v1 routes for C04 assembly: create a paper, assemble it into a draft version, publish it.

The tenant comes only from the signed JWT. Assembling is deterministic in `random_seed`; publishing freezes the
version and stores the grading key in its own table. None of these routes ever returns an answer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.ai_solver import AiSolver
from app.modules.assembly.internal.from_pdf import DURATIONS_MINUTES, TeacherPaperError, create_paper_from_pdf
from app.modules.assembly.internal.service import (
    AssemblyShortageFailed,
    AssessmentConflictError,
    AssessmentNotFoundError,
    AssessmentRecord,
    AssessmentVersionRecord,
    PublishGateBlockedError,
    TopicCount,
    assemble_assessment,
    create_assessment,
    list_assessments,
    pool_summary,
    publish_assessment,
)
from app.modules.questions.public import default_numeric_checker
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["assembly"])

PublicationStatusName = Literal["DRAFT", "READY", "PUBLISHED", "ARCHIVED", "TAKEN_DOWN"]
EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
# Who turns a PDF into a paper and an exam room: a teacher, or an administrator of the school.
TEACHER_PAPER_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})
TENANT_WIDE_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})
MAX_PDF_BYTES = 25 * 1024 * 1024


class CreateAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    subject_code: str = Field(min_length=1, max_length=50)
    grade_band: str = Field(min_length=1, max_length=30)
    exam_type_code: str = Field(min_length=1, max_length=50)
    visibility_scope: Literal["SYSTEM_PUBLIC", "TENANT_PRIVATE"] = "TENANT_PRIVATE"
    access_tier: Literal["FREE", "STUDENT_PRO", "TEACHER_PRO"] = "FREE"
    # What the author may claim; LICENSED and the rest come from a rights review (A09).
    rights_status: Literal["PENDING_REVIEW", "OWNED"] = "PENDING_REVIEW"


MatrixItemType = Literal["SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY"]


def _default_matrix_types() -> list[MatrixItemType]:
    return ["SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC"]


class TopicCountIn(BaseModel):
    """One line of the matrix: `question_count` questions of a topic (with its subtree)."""

    model_config = ConfigDict(extra="forbid")

    taxonomy_node_id: uuid.UUID
    question_count: int = Field(ge=1, le=100)
    difficulty_level: Literal["NB", "TH", "VD", "VDC"] | None = None
    item_types: list[MatrixItemType] = Field(default_factory=_default_matrix_types, min_length=1)


class AssembleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_code: str = Field(default="THPTQG_2025", min_length=1, max_length=60)
    subject_code: str | None = Field(default=None, min_length=1, max_length=50)
    random_seed: int = Field(ge=0, lt=2**62)
    taxonomy_node_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    # The teacher's matrix: when given, the paper is these topics x counts instead of the framework's sections.
    taxonomy_counts: list[TopicCountIn] = Field(default_factory=list, max_length=50)
    total_points: Decimal | None = Field(default=None, gt=0, le=100)
    duration_minutes: int | None = Field(default=None, ge=5, le=300)


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    title: str
    subject_code: str
    grade_band: str
    exam_type_code: str
    access_tier: str
    current_version_id: uuid.UUID | None
    verification_status: str
    rights_status: str
    publication_status: str
    created_at: datetime


class ItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_version_id: uuid.UUID
    section_code: str
    sequence_order: int
    points: Decimal


class VersionResponse(BaseModel):
    """A version with its answer-free structure and items."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    assessment_id: uuid.UUID
    version_no: int
    scoring_scheme_id: uuid.UUID
    duration_seconds: int
    total_points: Decimal
    base_seed: int
    is_immutable: bool
    published_at: datetime | None
    structure_snapshot: dict[str, Any]
    items: list[ItemResponse]


class PublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: AssessmentResponse
    version: VersionResponse
    answer_key_checksum: str
    publication_id: uuid.UUID
    event_id: uuid.UUID


def _assessment_response(record: AssessmentRecord) -> AssessmentResponse:
    return AssessmentResponse(**{f: getattr(record, f) for f in AssessmentResponse.model_fields})


def _version_response(record: AssessmentVersionRecord) -> VersionResponse:
    values = {f: getattr(record, f) for f in VersionResponse.model_fields if f != "items"}
    items = [ItemResponse(**{f: getattr(i, f) for f in ItemResponse.model_fields}) for i in record.items]
    return VersionResponse(items=items, **values)


def _problem(
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return problem_json(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, roles: frozenset[str] = EDITOR_ROLES
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if not ({r.upper() for r in actor.roles} & roles):
        return (
            None,
            correlation_id,
            _problem(
                PlatformErrorCode.FORBIDDEN, "an author or administrator role is required", request, correlation_id
            ),
        )
    return actor, correlation_id, None


def _service_error(exc: Exception, request: Request, correlation_id: str) -> JSONResponse:
    if isinstance(exc, AssessmentNotFoundError):
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    if isinstance(exc, AssessmentConflictError):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            request,
            correlation_id,
            status_override=status.HTTP_409_CONFLICT,
            extensions={"reason": exc.code},
        )
    if isinstance(exc, AssemblyShortageFailed):
        shortage = exc.shortage
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            request,
            correlation_id,
            status_override=422,
            extensions={
                "reason": "POOL_SHORTAGE",
                "section_code": shortage.section_code,
                "item_type": shortage.item_type,
                "required": shortage.required,
                "selected": shortage.selected,
                "candidates": shortage.candidates,
            },
        )
    if isinstance(exc, PublishGateBlockedError):
        return _problem(
            PlatformErrorCode.PUBLISH_GATE_BLOCKED,
            str(exc),
            request,
            correlation_id,
            extensions={"blocking_codes": list(exc.codes)},
        )
    return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)


@router.post("/assessments", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
async def post_assessment(body: CreateAssessmentRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Create a paper (no version yet): DRAFT, UNCHECKED, rights as claimed."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    if body.visibility_scope == "SYSTEM_PUBLIC" and actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            "SYSTEM_PUBLIC assessments can only be created in the system tenant",
            request,
            correlation_id,
        )
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        record = await create_assessment(
            session,
            tenant_id=actor.tenant_id,
            title=body.title,
            subject_code=body.subject_code,
            grade_band=body.grade_band,
            exam_type_code=body.exam_type_code,
            visibility_scope=body.visibility_scope,
            access_tier=body.access_tier,
            rights_status=body.rights_status,
        )
    return _assessment_response(record)


@router.get("/assessments", response_model=list[AssessmentResponse])
async def get_assessments(
    request: Request,
    publication_status: Annotated[PublicationStatusName, Query()] = "PUBLISHED",
    credentials: OptionalBearer = None,
) -> Any:
    """Papers of the tenant plus the SYSTEM_PUBLIC ones. Any signed-in member lists the published ones (that is
    what a learner picks a paper from); any other status needs an author or administrator role."""
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if publication_status != "PUBLISHED" and not ({r.upper() for r in actor.roles} & EDITOR_ROLES):
        return _problem(
            PlatformErrorCode.FORBIDDEN, "an author or administrator role is required", request, correlation_id
        )
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        records = await list_assessments(session, publication_status=publication_status)
    return [_assessment_response(r) for r in records]


@router.post("/assessments/{assessment_id}/assemble", response_model=VersionResponse)
async def post_assemble(
    assessment_id: uuid.UUID, body: AssembleRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Pick the questions for the framework and subject into a DRAFT version (replaces the previous draft)."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            version = await assemble_assessment(
                session,
                assessment_id,
                framework_code=body.framework_code,
                subject_code=body.subject_code,
                random_seed=body.random_seed,
                taxonomy_node_ids=body.taxonomy_node_ids,
                taxonomy_counts=[
                    TopicCount(
                        taxonomy_node_id=c.taxonomy_node_id,
                        question_count=c.question_count,
                        difficulty_level=c.difficulty_level,
                        item_types=tuple(c.item_types),
                    )
                    for c in body.taxonomy_counts
                ],
                total_points=body.total_points,
                duration_seconds=None if body.duration_minutes is None else body.duration_minutes * 60,
            )
    except (AssessmentNotFoundError, AssessmentConflictError, AssemblyShortageFailed) as exc:
        return _service_error(exc, request, correlation_id)
    return _version_response(version)


@router.post("/assessments/{assessment_id}/publish", response_model=PublishResponse)
async def post_publish(assessment_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """Freeze the draft version, store its grading key apart and publish the paper."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await publish_assessment(
                session, assessment_id, published_by=actor.user_id, correlation_id=correlation_id
            )
    except (AssessmentNotFoundError, AssessmentConflictError, PublishGateBlockedError) as exc:
        return _service_error(exc, request, correlation_id)
    return PublishResponse(
        assessment=_assessment_response(result.assessment),
        version=_version_response(result.version),
        answer_key_checksum=result.answer_key_checksum,
        publication_id=result.publication_id,
        event_id=result.event_id,
    )


class PoolTopicCountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_id: uuid.UUID
    code_path: str
    item_type: str
    difficulty_level: str
    available: int


@router.get("/assessments/pool-summary", response_model=list[PoolTopicCountResponse])
async def get_pool_summary(
    request: Request,
    subject_code: Annotated[str, Query(min_length=1, max_length=50)],
    credentials: OptionalBearer = None,
) -> Any:
    """How many drawable questions each topic holds, per item type and difficulty: what a matrix may ask for.

    Counts only, never a question: this is what a teacher sees of the bank.
    """
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        rows = await pool_summary(session, subject_code=subject_code)
    return [PoolTopicCountResponse(**{f: getattr(r, f) for f in PoolTopicCountResponse.model_fields}) for r in rows]


class TeacherPaperResponse(BaseModel):
    """What the teacher gets back: the paper and its room, never a question or an answer."""

    model_config = ConfigDict(extra="forbid")

    assessment_id: uuid.UUID
    assessment_version_id: uuid.UUID
    assignment_id: uuid.UUID | None
    title: str
    question_count: int
    skipped_question_count: int
    duration_minutes: int
    exam_room_url: str | None
    assignment_url: str | None


def get_paper_solver() -> AiSolver:
    """The configured AI solver; tests override this dependency."""
    return AiSolver(numeric_checker=default_numeric_checker())


@router.post("/teacher/assessments/from-pdf", response_model=TeacherPaperResponse, status_code=status.HTTP_201_CREATED)
async def post_teacher_paper_from_pdf(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=500)],
    subject_code: Annotated[str, Form(min_length=1, max_length=50)],
    grade_band: Annotated[str, Form(min_length=1, max_length=30)] = "GRADE_12",
    duration_minutes: Annotated[int, Form()] = 45,
    class_id: Annotated[uuid.UUID | None, Form()] = None,
    starts_at: Annotated[datetime | None, Form()] = None,
    due_at: Annotated[datetime | None, Form()] = None,
    credentials: OptionalBearer = None,
    solver: AiSolver = Depends(get_paper_solver),  # noqa: B008
) -> Any:
    """A teacher's PDF -> questions solved and silently added to the bank -> a published paper -> an open room.

    `duration_minutes` is one of 15, 30, 45, 60, 90, 120, 150, 180. With `class_id` (a class the caller runs) the
    paper is assigned to it at once (`starts_at` / `due_at` optional). 422 `reason`: INVALID_FILE,
    NO_QUESTIONS_FOUND, NO_USABLE_QUESTION, INVALID_DURATION; 404 for a class the teacher does not run.
    """
    actor, correlation_id, err = _authenticate(request, credentials, TEACHER_PAPER_ROLES)
    if err is not None:
        return err
    assert actor is not None
    raw = await file.read(MAX_PDF_BYTES + 1)
    if len(raw) > MAX_PDF_BYTES:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR, f"the PDF is larger than {MAX_PDF_BYTES} bytes", request,
            correlation_id, extensions={"reason": "FILE_TOO_LARGE"},
        )  # fmt: skip
    if duration_minutes not in DURATIONS_MINUTES:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR, f"duration_minutes must be one of {DURATIONS_MINUTES}", request,
            correlation_id, extensions={"reason": "INVALID_DURATION"},
        )  # fmt: skip
    tenant_wide = bool({r.upper() for r in actor.roles} & TENANT_WIDE_ROLES)
    try:
        # no transaction here: the function opens short ones around the database steps, never around the AI
        result = await create_paper_from_pdf(
            tenant_id=actor.tenant_id,
            teacher_id=actor.user_id,
            teacher_membership_id=actor.membership_id,
            tenant_wide=tenant_wide,
            raw_pdf=raw,
            filename=file.filename or "de-thi.pdf",
            title=title,
            subject_code=subject_code,
            grade_band=grade_band,
            duration_minutes=duration_minutes,
            solver=solver,
            class_id=class_id,
            starts_at=starts_at,
            due_at=due_at,
            correlation_id=correlation_id,
        )
    except TeacherPaperError as exc:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=422,
            extensions={"reason": exc.code},
        )  # fmt: skip
    except (AssessmentNotFoundError, AssessmentConflictError, PublishGateBlockedError) as exc:
        return _service_error(exc, request, correlation_id)
    class_url = None if result.class_id is None else f"/teacher/classes/{result.class_id}"
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=TeacherPaperResponse(
            assessment_id=result.assessment_id,
            assessment_version_id=result.assessment_version_id,
            assignment_id=result.assignment_id,
            title=result.title,
            question_count=result.question_count,
            skipped_question_count=result.skipped_question_count,
            duration_minutes=result.duration_minutes,
            exam_room_url=class_url,
            assignment_url=None if class_url is None else f"{class_url}?assignment_id={result.assignment_id}",
        ).model_dump(mode="json"),
        headers={"X-Correlation-Id": correlation_id},
    )
