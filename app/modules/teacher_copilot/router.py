"""FastAPI /api/v1 routes for E06 Teacher AI Copilot."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.teacher_copilot.internal.service import (
    TeacherAssistJobView,
    TeacherCopilotConflictError,
    TeacherCopilotNotFoundError,
    TeacherCopilotScopeError,
    TeacherCopilotValidationError,
    confirm_teacher_assist_draft,
    get_teacher_assist_job,
    run_teacher_ai_assist,
)
from app.modules.teacher_copilot.internal.teacher_assist import StudentMistakeObservation
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

router = APIRouter(prefix="/api/v1", tags=["teacher-copilot"])

_TEACHER_ROLES = frozenset({"TEACHER", "ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=64)
    class_id: str = Field(min_length=1, max_length=64)
    student_id: str = Field(min_length=1, max_length=64)
    taxonomy_node_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    chosen_option: str = Field(min_length=1, max_length=120)
    correct_option: str = Field(min_length=1, max_length=120)


class RunTeacherAssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: uuid.UUID
    assignment_id: uuid.UUID
    max_question_target: int = Field(default=10, ge=1, le=50)
    observations: list[ObservationInput] | None = None


class ConfirmDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str | None = Field(default=None, max_length=160)


class TeacherAssistJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    class_id: uuid.UUID
    assignment_id: uuid.UUID
    teacher_membership_id: uuid.UUID
    status: str
    clusters: list[dict[str, Any]]
    draft_assignments: list[dict[str, Any]]
    created_at: datetime


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


def _authenticate_teacher(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    correlation_id: str,
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

    if not any(r.upper() in _TEACHER_ROLES for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="teacher role is required to invoke Teacher AI Copilot",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize(view: TeacherAssistJobView) -> TeacherAssistJobResponse:
    return TeacherAssistJobResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        class_id=view.class_id,
        assignment_id=view.assignment_id,
        teacher_membership_id=view.teacher_membership_id,
        status=view.status,
        clusters=list(view.clusters),
        draft_assignments=list(view.draft_assignments),
        created_at=view.created_at,
    )


@router.post("/teacher/ai-assist")
async def run_teacher_ai_assist_endpoint(
    payload: RunTeacherAssistRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate_teacher(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    obs_tuple = (
        tuple(
            StudentMistakeObservation(
                tenant_id=o.tenant_id,
                class_id=o.class_id,
                student_id=o.student_id,
                taxonomy_node_id=o.taxonomy_node_id,
                question_id=o.question_id,
                chosen_option=o.chosen_option,
                correct_option=o.correct_option,
            )
            for o in payload.observations
        )
        if payload.observations is not None
        else None
    )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            job = await run_teacher_ai_assist(
                session,
                tenant_id=actor.tenant_id,
                actor_membership_id=actor.membership_id,
                class_id=payload.class_id,
                assignment_id=payload.assignment_id,
                observations=obs_tuple,
                max_question_target=payload.max_question_target,
            )
    except TeacherCopilotScopeError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TeacherCopilotNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TeacherCopilotValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"error_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize(job)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/teacher/ai-assist/{job_id}")
async def get_teacher_ai_assist_endpoint(
    job_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate_teacher(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            job = await get_teacher_assist_job(
                session,
                tenant_id=actor.tenant_id,
                actor_membership_id=actor.membership_id,
                job_id=job_id,
            )
    except TeacherCopilotScopeError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TeacherCopilotNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize(job)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/teacher/ai-assist/{job_id}/confirm")
async def confirm_teacher_ai_assist_endpoint(
    job_id: uuid.UUID,
    request: Request,
    payload: ConfirmDraftRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate_teacher(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    draft_id = payload.draft_id if payload is not None else None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            job = await confirm_teacher_assist_draft(
                session,
                tenant_id=actor.tenant_id,
                actor_membership_id=actor.membership_id,
                job_id=job_id,
                draft_id=draft_id,
            )
    except TeacherCopilotScopeError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TeacherCopilotNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TeacherCopilotConflictError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=409,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TeacherCopilotValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"error_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize(job)),
        headers={"X-Correlation-ID": correlation_id},
    )
