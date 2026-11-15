"""FastAPI /api/v1 routes for D02 Personalized Learning Path."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.learning_path.internal.remediation import DEFAULT_PREREQUISITE_MASTERY_THRESHOLD
from app.modules.learning_path.internal.service import (
    LearningPathNotFoundError,
    LearningPathValidationError,
    LearningPlanView,
    PlanTaskView,
    create_learning_plan,
    get_learning_plan,
    list_learning_plans,
    recalculate_learning_plan,
    update_plan_task_status,
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

router = APIRouter(prefix="/api/v1/learning-plans", tags=["learning-path"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})


class CreateLearningPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: uuid.UUID | None = None
    target_framework_code: str = Field(default="THPTQG_2025", min_length=1, max_length=60)
    weak_node_ids: list[uuid.UUID] | None = None
    mastery_overrides: dict[uuid.UUID, float] | None = None
    graph_version: int = Field(default=1, ge=1)
    prerequisite_mastery_threshold: float = Field(
        default=DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
        ge=0.0,
        le=1.0,
    )


class RecalculateLearningPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weak_node_ids: list[uuid.UUID] | None = None
    mastery_overrides: dict[uuid.UUID, float] | None = None
    graph_version: int = Field(default=1, ge=1)
    prerequisite_mastery_threshold: float = Field(
        default=DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
        ge=0.0,
        le=1.0,
    )


class UpdatePlanTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=20)


class PlanTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    learning_plan_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    task_type: str
    rationale_text: str
    sequence_order: int
    status: str


class LearningPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    target_framework_code: str
    status: str
    created_at: datetime
    tasks: list[PlanTaskResponse]


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return problem_json(
        code=code,
        detail=detail,
        instance=instance,
        correlation_id=correlation_id,
        status_override=status_code,
        extensions=extensions,
    )


def _authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    correlation_id: str,
) -> ActorContext | JSONResponse:
    get_settings()
    try:
        actor = verify_actor(request, credentials, allow_cookie=True)
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
    return actor


def _is_staff(actor: ActorContext) -> bool:
    return bool({r.upper() for r in actor.roles} & _STAFF_ROLES)


def _serialize_task(task: PlanTaskView) -> PlanTaskResponse:
    return PlanTaskResponse(
        id=task.id,
        tenant_id=task.tenant_id,
        learning_plan_id=task.learning_plan_id,
        taxonomy_node_id=task.taxonomy_node_id,
        task_type=task.task_type,
        rationale_text=task.rationale_text,
        sequence_order=task.sequence_order,
        status=task.status,
    )


def _serialize_plan(plan: LearningPlanView) -> LearningPlanResponse:
    return LearningPlanResponse(
        id=plan.id,
        tenant_id=plan.tenant_id,
        membership_id=plan.membership_id,
        target_framework_code=plan.target_framework_code,
        status=plan.status,
        created_at=plan.created_at,
        tasks=[_serialize_task(t) for t in plan.tasks],
    )


@router.post("", response_model=LearningPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan_endpoint(
    payload: CreateLearningPlanRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    target_membership = payload.membership_id or auth.membership_id
    if target_membership != auth.membership_id and not _is_staff(auth):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="learners can only create plans for their own membership",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            plan = await create_learning_plan(
                session,
                tenant_id=auth.tenant_id,
                membership_id=target_membership,
                target_framework_code=payload.target_framework_code,
                weak_node_ids=payload.weak_node_ids,
                mastery_overrides=payload.mastery_overrides,
                graph_version=payload.graph_version,
                prerequisite_mastery_threshold=payload.prerequisite_mastery_threshold,
            )
    except LearningPathNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except LearningPathValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_plan(plan)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("", response_model=list[LearningPlanResponse])
async def list_plans_endpoint(
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    membership_filter = None if _is_staff(auth) else auth.membership_id
    async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
        plans = await list_learning_plans(
            session,
            tenant_id=auth.tenant_id,
            membership_id=membership_filter,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder([_serialize_plan(p) for p in plans]),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{plan_id}", response_model=LearningPlanResponse)
async def get_plan_endpoint(
    plan_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            plan = await get_learning_plan(session, tenant_id=auth.tenant_id, plan_id=plan_id)
    except LearningPathNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    if plan.membership_id != auth.membership_id and not _is_staff(auth):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="cannot inspect another learner's learning plan",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_plan(plan)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{plan_id}/recalculate", response_model=LearningPlanResponse)
async def recalculate_plan_endpoint(
    plan_id: uuid.UUID,
    payload: RecalculateLearningPlanRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            existing = await get_learning_plan(session, tenant_id=auth.tenant_id, plan_id=plan_id)
            if existing.membership_id != auth.membership_id and not _is_staff(auth):
                return _problem_response(
                    code=PlatformErrorCode.FORBIDDEN,
                    detail="cannot recalculate another learner's learning plan",
                    status_code=403,
                    instance=str(request.url.path),
                    correlation_id=correlation_id,
                )
            plan = await recalculate_learning_plan(
                session,
                tenant_id=auth.tenant_id,
                plan_id=plan_id,
                weak_node_ids=payload.weak_node_ids,
                mastery_overrides=payload.mastery_overrides,
                graph_version=payload.graph_version,
                prerequisite_mastery_threshold=payload.prerequisite_mastery_threshold,
            )
    except LearningPathNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except LearningPathValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_plan(plan)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.patch("/{plan_id}/tasks/{task_id}", response_model=LearningPlanResponse)
async def update_plan_task_endpoint(
    plan_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: UpdatePlanTaskRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            existing = await get_learning_plan(session, tenant_id=auth.tenant_id, plan_id=plan_id)
            if existing.membership_id != auth.membership_id and not _is_staff(auth):
                return _problem_response(
                    code=PlatformErrorCode.FORBIDDEN,
                    detail="cannot modify another learner's learning plan task",
                    status_code=403,
                    instance=str(request.url.path),
                    correlation_id=correlation_id,
                )
            plan = await update_plan_task_status(
                session,
                tenant_id=auth.tenant_id,
                plan_id=plan_id,
                task_id=task_id,
                status=payload.status,
            )
    except LearningPathNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except LearningPathValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_plan(plan)),
        headers={"X-Correlation-ID": correlation_id},
    )
