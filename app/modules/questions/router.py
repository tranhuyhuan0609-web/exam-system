"""FastAPI /api/v1 routes for C01 questions: list and read the bank, create a question, fork a version, validate one.

The tenant comes only from the signed JWT. Every version is checked by the typed authoring core (6 item types)
before it is stored; answers are written to `question_version_answers` and never returned (nor read) by these routes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.questions.internal.service import (
    OptionInput,
    PublishGateBlockedError,
    QuestionConflictError,
    QuestionDetail,
    QuestionListItem,
    QuestionNotFoundError,
    QuestionRecord,
    QuestionValidationFailedError,
    QuestionVersionRecord,
    StimulusInput,
    VersionSummary,
    create_next_version,
    create_question,
    get_question,
    get_question_detail,
    list_question_versions,
    list_questions,
    publish_question_version,
    validate_question_version,
)
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.pagination import CursorError, resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["questions"])

# The question bank is run by authors, reviewers and administrators. A teacher builds papers from it (C04) and
# turns a PDF into a paper (`POST /teacher/assessments/from-pdf`) but never reads or edits the bank itself.
EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "CONTENT_AUTHOR", "REVIEWER"})
STABLE_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class OptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=10)
    text: str = Field(min_length=1, max_length=20_000)


class StimulusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stimulus_type: Literal["READING_PASSAGE", "EXPERIMENT_CONTEXT", "DATA_TABLE", "SHARED_FIGURE"]
    body: dict[str, Any]


class CreateQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_code: str = Field(min_length=1, max_length=100, pattern=STABLE_CODE_PATTERN)
    item_type: Literal["SINGLE_CHOICE", "MULTIPLE_SELECT", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY", "GROUP_STEM"]
    difficulty_level: Literal["NB", "TH", "VD", "VDC"] = "TH"
    stem_text: str = Field(min_length=1, max_length=50_000)
    options: list[OptionIn] = Field(default_factory=list, max_length=10)
    answer_schema: dict[str, Any]
    rationale: dict[str, Any] | None = None
    stimulus_id: uuid.UUID | None = None
    stimulus: StimulusIn | None = None
    primary_taxonomy_node_id: uuid.UUID | None = None
    curriculum_node_id: uuid.UUID | None = None
    exam_framework_tags: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=lambda: ["SGK_KNTT"], min_length=1, max_length=20
    )
    visibility_scope: Literal["SYSTEM_PUBLIC", "TENANT_PRIVATE", "SHARED_PARTNER"] = "TENANT_PRIVATE"
    access_tier: Literal["FREE", "STUDENT_PRO", "TEACHER_PRO"] = "FREE"
    # What the author may claim; LICENSED and the rest come from a rights review (A09).
    rights_status: Literal["PENDING_REVIEW", "OWNED"] = "PENDING_REVIEW"
    asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class PublishQuestionRequest(BaseModel):
    """`version_id` left out publishes the newest version; it must be the newest (older drafts are superseded)."""

    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID | None = None


class CreateVersionRequest(BaseModel):
    """Fields left out keep the value of the version being forked; the item type never changes."""

    model_config = ConfigDict(extra="forbid")

    change_reason: str = Field(min_length=1, max_length=500)
    stem_text: str | None = Field(default=None, min_length=1, max_length=50_000)
    options: list[OptionIn] | None = Field(default=None, max_length=10)
    answer_schema: dict[str, Any] | None = None
    rationale: dict[str, Any] | None = None
    asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class QuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    stable_code: str
    item_type: str
    primary_taxonomy_node_id: uuid.UUID | None
    curriculum_node_id: uuid.UUID | None
    exam_framework_tags: list[str]
    difficulty_level: str
    current_version_id: uuid.UUID | None
    acquisition_status: str
    verification_status: str
    rights_status: str
    publication_status: str
    access_tier: str
    created_at: datetime


class QuestionVersionResponse(BaseModel):
    """A version without its answer."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    question_id: uuid.UUID
    version_no: int
    visibility_scope: str
    stimulus_id: uuid.UUID | None
    stem: dict[str, Any]
    options: list[dict[str, Any]]
    numeric_parse_policy: dict[str, Any]
    content_hash: str
    structural_hash: str
    is_immutable: bool
    change_reason: str | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    created_at: datetime


class QuestionListItemResponse(QuestionResponse):
    """A question of the bank list with the version it shows (the published one, else the newest draft)."""

    version_id: uuid.UUID
    version_no: int
    is_immutable: bool
    stimulus_id: uuid.UUID | None
    stem_text: str
    options: list[dict[str, Any]]


class QuestionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[QuestionListItemResponse]
    next_cursor: str | None
    has_more: bool
    limit: int


class VersionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    version_no: int
    is_immutable: bool
    is_current: bool
    change_reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class QuestionDetailResponse(BaseModel):
    """One question: its row, the version the bank shows (no answer) and the list of its versions."""

    model_config = ConfigDict(extra="forbid")

    question: QuestionResponse
    shown_version: QuestionVersionResponse | None
    versions: list[VersionSummaryResponse]


class PublishQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: QuestionResponse
    version: QuestionVersionResponse
    publication_id: uuid.UUID
    event_id: uuid.UUID


class CreateQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: QuestionResponse
    version: QuestionVersionResponse


class ValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID
    valid: bool
    code: str | None
    message: str | None


def _question_response(question: QuestionRecord) -> QuestionResponse:
    values = {f: getattr(question, f) for f in QuestionResponse.model_fields if f != "exam_framework_tags"}
    return QuestionResponse(exam_framework_tags=list(question.exam_framework_tags), **values)


def _version_response(version: QuestionVersionRecord) -> QuestionVersionResponse:
    values = {f: getattr(version, f) for f in QuestionVersionResponse.model_fields if f != "options"}
    return QuestionVersionResponse(options=list(version.options), **values)


def _list_item_response(item: QuestionListItem) -> QuestionListItemResponse:
    base = _question_response(item.question).model_dump()
    return QuestionListItemResponse(
        **base,
        version_id=item.version_id,
        version_no=item.version_no,
        is_immutable=item.is_immutable,
        stimulus_id=item.stimulus_id,
        stem_text=item.stem_text,
        options=list(item.options),
    )


def _detail_response(detail: QuestionDetail) -> QuestionDetailResponse:
    def summary(v: VersionSummary) -> VersionSummaryResponse:
        return VersionSummaryResponse(**{f: getattr(v, f) for f in VersionSummaryResponse.model_fields})

    return QuestionDetailResponse(
        question=_question_response(detail.question),
        shown_version=None if detail.shown_version is None else _version_response(detail.shown_version),
        versions=[summary(v) for v in detail.versions],
    )


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
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if not ({r.upper() for r in actor.roles} & EDITOR_ROLES):
        return (
            None,
            correlation_id,
            _problem(
                PlatformErrorCode.FORBIDDEN, "an author or administrator role is required", request, correlation_id
            ),
        )
    return actor, correlation_id, None


def _service_error(exc: Exception, request: Request, correlation_id: str) -> JSONResponse:
    if isinstance(exc, QuestionNotFoundError):
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    if isinstance(exc, QuestionConflictError):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            request,
            correlation_id,
            status_override=status.HTTP_409_CONFLICT,
            extensions={"reason": exc.code},
        )
    if isinstance(exc, PublishGateBlockedError):
        return _problem(
            PlatformErrorCode.PUBLISH_GATE_BLOCKED,
            str(exc),
            request,
            correlation_id,
            extensions={"blocking_codes": list(exc.codes)},
        )
    if isinstance(exc, QuestionValidationFailedError):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            request,
            correlation_id,
            status_override=422,
            extensions={"reason": exc.code},
        )
    return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)


def _options(options: list[OptionIn] | None) -> tuple[OptionInput, ...] | None:
    return None if options is None else tuple(OptionInput(key=o.key, text=o.text) for o in options)


QuestionItemTypeName = Literal[
    "SINGLE_CHOICE", "MULTIPLE_SELECT", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY", "GROUP_STEM"
]
DifficultyName = Literal["NB", "TH", "VD", "VDC"]
PublicationStatusName = Literal["DRAFT", "READY", "PUBLISHED", "ARCHIVED", "TAKEN_DOWN"]


@router.get("/questions", response_model=QuestionListResponse)
async def get_questions(
    request: Request,
    q: Annotated[str | None, Query(max_length=200, description="text in the stem or in the stable_code")] = None,
    item_type: Annotated[QuestionItemTypeName | None, Query()] = None,
    difficulty_level: Annotated[DifficultyName | None, Query()] = None,
    curriculum_node_id: Annotated[uuid.UUID | None, Query(description="this node and all under it")] = None,
    taxonomy_node_id: Annotated[uuid.UUID | None, Query(description="this node and all under it")] = None,
    publication_status: Annotated[PublicationStatusName | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    credentials: OptionalBearer = None,
) -> Any:
    """The bank of the tenant and the SYSTEM_PUBLIC questions, newest first, with stem and options, never an answer."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            page = await list_questions(
                session,
                tenant_id=actor.tenant_id,
                cursor_secret=get_settings().secret_key,
                q=q,
                item_type=item_type,
                difficulty_level=difficulty_level,
                curriculum_node_id=curriculum_node_id,
                taxonomy_node_id=taxonomy_node_id,
                publication_status=publication_status,
                limit=limit,
                cursor=cursor,
            )
    except (CursorError, ValueError) as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    return QuestionListResponse(
        items=[_list_item_response(i) for i in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        limit=page.limit,
    )


@router.get("/questions/{question_id}", response_model=QuestionDetailResponse)
async def get_question_by_id(question_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """One question of the tenant (or a SYSTEM_PUBLIC one) with its versions; another tenant's id is a 404."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            detail = await get_question_detail(session, question_id)
    except QuestionNotFoundError as exc:
        return _service_error(exc, request, correlation_id)
    return _detail_response(detail)


@router.post("/questions", response_model=CreateQuestionResponse, status_code=status.HTTP_201_CREATED)
async def post_question(body: CreateQuestionRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Create a question with its first version (a draft) and its answer."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    if body.visibility_scope == "SYSTEM_PUBLIC" and actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            "SYSTEM_PUBLIC questions can only be created in the system tenant",
            request,
            correlation_id,
        )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            question, version = await create_question(
                session,
                tenant_id=actor.tenant_id,
                created_by=actor.user_id,
                stable_code=body.stable_code,
                item_type=body.item_type,
                difficulty_level=body.difficulty_level,
                stem_text=body.stem_text,
                options=_options(body.options) or (),
                answer_schema=body.answer_schema,
                rationale=body.rationale,
                stimulus_id=body.stimulus_id,
                stimulus=None
                if body.stimulus is None
                else StimulusInput(stimulus_type=body.stimulus.stimulus_type, body=body.stimulus.body),
                primary_taxonomy_node_id=body.primary_taxonomy_node_id,
                curriculum_node_id=body.curriculum_node_id,
                exam_framework_tags=body.exam_framework_tags,
                visibility_scope=body.visibility_scope,
                access_tier=body.access_tier,
                rights_status=body.rights_status,
                asset_ids=body.asset_ids,
            )
    except (QuestionNotFoundError, QuestionConflictError, QuestionValidationFailedError, ValueError) as exc:
        return _service_error(exc, request, correlation_id)
    return CreateQuestionResponse(question=_question_response(question), version=_version_response(version))


@router.post(
    "/questions/{question_id}/versions", response_model=QuestionVersionResponse, status_code=status.HTTP_201_CREATED
)
async def post_question_version(
    question_id: uuid.UUID, body: CreateVersionRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Start `version_no + 1` (a draft) of a published question: a published version never changes."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            version = await create_next_version(
                session,
                question_id,
                created_by=actor.user_id,
                change_reason=body.change_reason,
                stem_text=body.stem_text,
                options=_options(body.options),
                answer_schema=body.answer_schema,
                rationale=body.rationale,
                asset_ids=body.asset_ids,
            )
    except (QuestionNotFoundError, QuestionConflictError, QuestionValidationFailedError, ValueError) as exc:
        return _service_error(exc, request, correlation_id)
    return _version_response(version)


@router.post("/questions/{question_id}/publish", response_model=PublishQuestionResponse)
async def post_publish_question(
    question_id: uuid.UUID,
    request: Request,
    body: PublishQuestionRequest | None = None,
    credentials: OptionalBearer = None,
) -> Any:
    """Publish the newest draft version of a question of the tenant: it is frozen with its answer, and the question
    becomes PUBLISHED so papers can draw it.

    The gate is the A09 one (the same as `chk_question_publish_gate`): the question must be VERIFIED by a human review
    decision (`POST /review-tasks` on the version, then `/review-tasks/{id}/decisions` APPROVE), its rights cleared
    (OWNED, LICENSED or ALLOWED_DERIVATIVE), and the version valid. `422 PUBLISH_GATE_BLOCKED` lists `blocking_codes`.
    """
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    requested = None if body is None else body.version_id
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            question = await get_question(session, question_id)
            # SYSTEM_PUBLIC rows are visible to every tenant but are not theirs to publish: same answer as missing.
            if question is None or question.tenant_id != actor.tenant_id:
                raise QuestionNotFoundError("question not found")
            versions = await list_question_versions(session, question_id)
            if not versions:
                raise QuestionNotFoundError("question has no version")
            if requested is None:
                version_id = max(versions, key=lambda v: v.version_no).id
            elif any(v.id == requested for v in versions):
                version_id = requested
            else:
                raise QuestionNotFoundError("question version not found")
            result = await publish_question_version(
                session,
                version_id,
                approved_by=actor.user_id,
                correlation_id=correlation_id,
                require_version_review=True,
            )
    except (
        QuestionNotFoundError,
        QuestionConflictError,
        QuestionValidationFailedError,
        PublishGateBlockedError,
    ) as exc:
        return _service_error(exc, request, correlation_id)
    return PublishQuestionResponse(
        question=_question_response(result.question),
        version=_version_response(result.version),
        publication_id=result.publication_id,
        event_id=result.event_id,
    )


@router.post("/question-versions/{version_id}/validate", response_model=ValidationResponse)
async def post_validate_question_version(
    version_id: uuid.UUID, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Re-run the typed checks on a stored version and its answer; reports, changes nothing."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            report = await validate_question_version(session, version_id)
    except QuestionNotFoundError as exc:
        return _service_error(exc, request, correlation_id)
    return ValidationResponse(
        version_id=report.version_id, valid=report.valid, code=report.code, message=report.message
    )
