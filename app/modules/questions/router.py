"""FastAPI /api/v1 routes for C01 questions: create a question, fork a new version, validate a version.

The tenant comes only from the signed JWT. Every version is checked by the typed authoring core (6 item types)
before it is stored; answers are written to `question_version_answers` and never returned by these routes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.modules.questions.internal.service import (
    OptionInput,
    QuestionConflictError,
    QuestionNotFoundError,
    QuestionRecord,
    QuestionValidationFailedError,
    QuestionVersionRecord,
    StimulusInput,
    create_next_version,
    create_question,
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
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["questions"])

EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
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
