"""FastAPI /api/v1 routes for C04 assembly: create a paper, assemble it into a draft version, publish it.

The tenant comes only from the signed JWT. Assembling is deterministic in `random_seed`; publishing freezes the
version and stores the grading key in its own table. None of these routes ever returns an answer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.modules.assembly.internal.service import (
    AssemblyShortageFailed,
    AssessmentConflictError,
    AssessmentNotFoundError,
    AssessmentRecord,
    AssessmentVersionRecord,
    PublishGateBlockedError,
    assemble_assessment,
    create_assessment,
    publish_assessment,
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

router = APIRouter(prefix="/api/v1", tags=["assembly"])

EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})


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


class AssembleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_code: str = Field(default="THPTQG_2025", min_length=1, max_length=60)
    subject_code: str | None = Field(default=None, min_length=1, max_length=50)
    random_seed: int = Field(ge=0, lt=2**62)
    taxonomy_node_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


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
