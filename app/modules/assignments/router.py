"""FastAPI /api/v1 routes for F02 Assessment Assignments, Windows & Reveal Policies."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.assignments.internal.schedule import RevealPolicy
from app.modules.assignments.internal.service import (
    AssignmentNotFoundError,
    AssignmentValidationError,
    AssignmentView,
    RevealStatusResult,
    StartCheckResult,
    check_can_start_assignment,
    create_assignment,
    get_assignment,
    get_assignment_reveal_status,
    update_assignment,
)
from app.modules.classes.public import ClassroomPolicyError
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
)
from app.platform.errors import PlatformErrorCode, build_problem_detail
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1/assignments", tags=["assignments"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class CreateAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_version_id: uuid.UUID
    class_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    available_from: datetime | None = None
    due_at: datetime | None = None
    max_attempts: int = Field(default=1, ge=1)
    reveal_policy: RevealPolicy = RevealPolicy.AFTER_DUE


class UpdateAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_from: datetime | None = None
    due_at: datetime | None = None
    clear_due_at: bool = False
    max_attempts: int | None = Field(default=None, ge=1)
    reveal_policy: RevealPolicy | None = None
    manually_released: bool | None = None


class StartCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: datetime | None = None
    attempts_used: int | None = Field(default=None, ge=0)
    allow_late_submission: bool = False


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    assessment_version_id: uuid.UUID
    class_id: uuid.UUID | None
    group_id: uuid.UUID | None
    assigned_by: uuid.UUID
    available_from: datetime
    due_at: datetime | None
    max_attempts: int
    reveal_policy: str
    manually_released: bool
    created_at: datetime
    active_recipient_membership_ids: list[uuid.UUID]


class StartCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: uuid.UUID
    allowed: bool
    attempts_used: int
    max_attempts: int
    available_from: datetime
    due_at: datetime | None


class RevealStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: uuid.UUID
    reveal_policy: str
    learner_submitted: bool
    manually_released: bool
    can_reveal_answers: bool
    due_at: datetime | None


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        status_override=status_code,
        instance=instance,
        correlation_id=correlation_id,
        extensions=extensions,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


def _authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    correlation_id: str,
    *,
    staff_only: bool = False,
) -> ActorContext | JSONResponse:
    settings = get_settings()
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    if not raw_auth and request.cookies.get("ulp_access_token"):
        raw_auth = f"Bearer {request.cookies['ulp_access_token']}"
    try:
        token = extract_bearer_token(raw_auth)
        actor = decode_and_verify_actor_token(token, secret=settings.secret_key, now_epoch=int(time.time()))
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
    except TenantSpoofingError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except AuthTokenError as exc:
        return _problem_response(
            code=PlatformErrorCode.UNAUTHORIZED,
            detail=str(exc),
            status_code=401,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    if staff_only and not any(role.upper() in _STAFF_ROLES for role in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff or teacher role is required for assignment administration",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_assignment(view: AssignmentView) -> AssignmentResponse:
    return AssignmentResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        assessment_version_id=view.assessment_version_id,
        class_id=view.class_id,
        group_id=view.group_id,
        assigned_by=view.assigned_by,
        available_from=view.available_from,
        due_at=view.due_at,
        max_attempts=view.max_attempts,
        reveal_policy=view.reveal_policy,
        manually_released=view.manually_released,
        created_at=view.created_at,
        active_recipient_membership_ids=list(view.active_recipient_membership_ids),
    )


def _serialize_start(res: StartCheckResult) -> StartCheckResponse:
    return StartCheckResponse(
        assignment_id=res.assignment_id,
        allowed=res.allowed,
        attempts_used=res.attempts_used,
        max_attempts=res.max_attempts,
        available_from=res.available_from,
        due_at=res.due_at,
    )


def _serialize_reveal(res: RevealStatusResult) -> RevealStatusResponse:
    return RevealStatusResponse(
        assignment_id=res.assignment_id,
        reveal_policy=res.reveal_policy,
        learner_submitted=res.learner_submitted,
        manually_released=res.manually_released,
        can_reveal_answers=res.can_reveal_answers,
        due_at=res.due_at,
    )


@router.post("", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
async def create_assignment_endpoint(
    payload: CreateAssignmentRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, staff_only=True)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            created = await create_assignment(
                session,
                tenant_id=auth.tenant_id,
                assessment_version_id=payload.assessment_version_id,
                assigned_by=auth.user_id,
                class_id=payload.class_id,
                group_id=payload.group_id,
                available_from=payload.available_from,
                due_at=payload.due_at,
                max_attempts=payload.max_attempts,
                reveal_policy=payload.reveal_policy,
            )
    except AssignmentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except AssignmentValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_assignment(created)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{assignment_id}", response_model=AssignmentResponse)
async def get_assignment_endpoint(
    assignment_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            found = await get_assignment(session, tenant_id=auth.tenant_id, assignment_id=assignment_id)
    except AssignmentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_assignment(found)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.patch("/{assignment_id}", response_model=AssignmentResponse)
async def update_assignment_endpoint(
    assignment_id: uuid.UUID,
    payload: UpdateAssignmentRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, staff_only=True)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            updated = await update_assignment(
                session,
                tenant_id=auth.tenant_id,
                assignment_id=assignment_id,
                available_from=payload.available_from,
                due_at=payload.due_at,
                clear_due_at=payload.clear_due_at,
                max_attempts=payload.max_attempts,
                reveal_policy=payload.reveal_policy,
                manually_released=payload.manually_released,
            )
    except AssignmentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except AssignmentValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_assignment(updated)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{assignment_id}/start-check", response_model=StartCheckResponse)
async def start_check_endpoint(
    assignment_id: uuid.UUID,
    request: Request,
    payload: StartCheckRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    opts = payload or StartCheckRequest()
    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            result = await check_can_start_assignment(
                session,
                tenant_id=auth.tenant_id,
                assignment_id=assignment_id,
                membership_id=auth.membership_id,
                now=opts.now,
                attempts_used=opts.attempts_used,
                allow_late_submission=opts.allow_late_submission,
            )
    except AssignmentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ClassroomPolicyError as exc:
        status_code = 422 if exc.code in {"NAIVE_TIMESTAMP", "INVALID_ATTEMPTS_USED"} else 403
        code = PlatformErrorCode.VALIDATION_ERROR if status_code == 422 else PlatformErrorCode.FORBIDDEN
        return _problem_response(
            code=code,
            detail=str(exc),
            status_code=status_code,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_start(result)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{assignment_id}/reveal-status", response_model=RevealStatusResponse)
async def reveal_status_endpoint(
    assignment_id: uuid.UUID,
    request: Request,
    now: Annotated[datetime | None, Query()] = None,
    learner_submitted: Annotated[bool | None, Query()] = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            result = await get_assignment_reveal_status(
                session,
                tenant_id=auth.tenant_id,
                assignment_id=assignment_id,
                membership_id=auth.membership_id,
                now=now,
                learner_submitted=learner_submitted,
            )
    except AssignmentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ClassroomPolicyError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_reveal(result)),
        headers={"X-Correlation-ID": correlation_id},
    )
