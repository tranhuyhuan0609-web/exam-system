"""FastAPI /api/v1 routes for C07 grading: grade or regrade an attempt, release a grade, file an appeal.

Grading runs as `ulp_worker_grading` (the only runtime role that reads answer keys); releasing and appeals run as
`ulp_app_rw`, which can stamp `released_at` but cannot change a score. A regrade for an appeal resolves that appeal
once the new revision is committed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.modules.grading.internal.service import (
    AppealRecord,
    GradeRecord,
    GradingConflictError,
    GradingNotFoundError,
    check_open_appeal,
    file_appeal,
    grade_attempt,
    override_grade,
    release_grade,
    resolve_appeal,
)
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["grading"])

GRADER_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})


class GradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade_reason: Literal["INITIAL", "APPEAL_REGRADE", "ERRATA_REGRADE", "MANUAL_OVERRIDE"] = "INITIAL"
    appeal_id: uuid.UUID | None = None
    resolution_note: str | None = Field(default=None, max_length=2000)


class OverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_score: Decimal = Field(ge=0, le=1000, max_digits=6, decimal_places=2)
    reason: str = Field(min_length=15, max_length=2000)


class AppealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_text: str = Field(min_length=10, max_length=4000)
    question_version_id: uuid.UUID | None = None


class GradeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    attempt_id: uuid.UUID
    revision_no: int
    is_current: bool
    grade_reason: str
    total_score: Decimal
    section_breakdown: dict[str, Any]
    items: list[dict[str, Any]]
    graded_by: uuid.UUID | None
    graded_at: datetime
    released_at: datetime | None


class AppealResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    grade_id: uuid.UUID
    question_version_id: uuid.UUID | None
    reason_text: str
    status: str
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None


def _grade_response(record: GradeRecord) -> GradeResponse:
    values = {f: getattr(record, f) for f in GradeResponse.model_fields if f != "items"}
    return GradeResponse(items=list(record.items), **values)


def _appeal_response(record: AppealRecord) -> AppealResponse:
    return AppealResponse(**{f: getattr(record, f) for f in AppealResponse.model_fields})


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
    request: Request, credentials: HTTPAuthorizationCredentials | None, *, grader: bool
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if grader and not ({r.upper() for r in actor.roles} & GRADER_ROLES):
        return (
            None,
            correlation_id,
            _problem(
                PlatformErrorCode.FORBIDDEN, "a teacher or administrator role is required", request, correlation_id
            ),
        )
    return actor, correlation_id, None


def _service_error(exc: Exception, request: Request, correlation_id: str) -> JSONResponse:
    if isinstance(exc, GradingNotFoundError):
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    if isinstance(exc, GradingConflictError):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            request,
            correlation_id,
            status_override=status.HTTP_409_CONFLICT,
            extensions={"reason": exc.code},
        )
    return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)


@router.post("/attempts/{attempt_id}/grade", response_model=GradeResponse, status_code=status.HTTP_201_CREATED)
async def post_grade(
    attempt_id: uuid.UUID, body: GradeRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Grade a submitted attempt (revision 1) or regrade it (revision + 1, the previous one kept, not current)."""
    actor, correlation_id, err = _authenticate(request, credentials, grader=True)
    if err is not None:
        return err
    assert actor is not None
    if body.appeal_id is not None and body.grade_reason != "APPEAL_REGRADE":
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            "appeal_id goes with grade_reason APPEAL_REGRADE",
            request,
            correlation_id,
        )
    try:
        if body.appeal_id is not None:
            async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
                await check_open_appeal(session, body.appeal_id, attempt_id)
        async with tenant_transaction(actor.tenant_id, role=DbRole.WORKER_GRADING) as session:
            grade = await grade_attempt(
                session,
                attempt_id,
                graded_by=actor.user_id,
                grade_reason=body.grade_reason,
                correlation_id=correlation_id,
            )
        if body.appeal_id is not None:
            async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
                await resolve_appeal(
                    session,
                    body.appeal_id,
                    resolved_by=actor.user_id,
                    status="APPROVED_REGRADED",
                    resolution_note=body.resolution_note or f"regraded as revision {grade.revision_no}",
                )
    except (GradingNotFoundError, GradingConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    return _grade_response(grade)


@router.post("/grades/{grade_id}/release", response_model=GradeResponse)
async def post_release(grade_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """Release the current grade to the student (once; releasing again returns it unchanged)."""
    actor, correlation_id, err = _authenticate(request, credentials, grader=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            grade, _ = await release_grade(session, grade_id, correlation_id=correlation_id)
    except (GradingNotFoundError, GradingConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    return _grade_response(grade)


@router.post("/grades/{grade_id}/override", response_model=GradeResponse, status_code=status.HTTP_201_CREATED)
async def post_override(
    grade_id: uuid.UUID, body: OverrideRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Set the total of the current grade by hand (MANUAL_OVERRIDE, revision + 1): a reason is required, audited."""
    actor, correlation_id, err = _authenticate(request, credentials, grader=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.WORKER_GRADING) as session:
            grade = await override_grade(
                session,
                grade_id,
                total_score=body.total_score,
                reason=body.reason,
                actor_id=actor.user_id,
                correlation_id=correlation_id,
            )
    except (GradingNotFoundError, GradingConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_grade_response(grade)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/grades/{grade_id}/appeals", response_model=AppealResponse, status_code=status.HTTP_201_CREATED)
async def post_appeal(
    grade_id: uuid.UUID, body: AppealRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """The student of the attempt appeals its released grade."""
    actor, correlation_id, err = _authenticate(request, credentials, grader=False)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            appeal = await file_appeal(
                session,
                grade_id,
                membership_id=actor.membership_id,
                reason_text=body.reason_text,
                question_version_id=body.question_version_id,
            )
    except (GradingNotFoundError, GradingConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_appeal_response(appeal)),
        headers={"X-Correlation-Id": correlation_id},
    )
