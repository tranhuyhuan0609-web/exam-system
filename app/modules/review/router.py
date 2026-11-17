"""FastAPI /api/v1 routes for A09: review tasks, reviewer decisions and source rights records."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.review.internal.service import (
    ReviewConflictError,
    ReviewNotFoundError,
    create_review_task,
    create_rights_record,
    decide_review_task,
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

router = APIRouter(prefix="/api/v1", tags=["review"])


REVIEWER_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "CONTENT_AUTHOR", "REVIEWER"})
RIGHTS_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(max_length=40)
    entity_id: uuid.UUID
    queue_name: str = Field(min_length=1, max_length=60)
    severity: str = Field(default="NORMAL", max_length=20)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(max_length=30)
    reason: str = Field(min_length=1, max_length=4000)


class RightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    license_type: str = Field(min_length=1, max_length=60)
    allow_rebranding: bool = False
    allow_public_redistribution: bool = False
    evidence_asset_id: uuid.UUID | None = None


def _problem(
    request: Request,
    correlation_id: str,
    code: PlatformErrorCode,
    detail: str,
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
    request: Request, credentials: HTTPAuthorizationCredentials | None, allowed: frozenset[str], what: str
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(request, correlation_id, PlatformErrorCode.UNAUTHORIZED, str(exc))
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(request, correlation_id, PlatformErrorCode.FORBIDDEN, str(exc))
    if not ({r.upper() for r in actor.roles} & allowed):
        return (
            None,
            correlation_id,
            _problem(request, correlation_id, PlatformErrorCode.FORBIDDEN, f"a reviewer role is required to {what}"),
        )
    return actor, correlation_id, None


def _conflict(request: Request, correlation_id: str, exc: ReviewConflictError) -> JSONResponse:
    return _problem(
        request,
        correlation_id,
        PlatformErrorCode.VALIDATION_ERROR,
        str(exc),
        status_override=status.HTTP_409_CONFLICT,
        extensions={"reason": exc.code},
    )


@router.post("/review-tasks", status_code=status.HTTP_201_CREATED)
async def create_task_endpoint(request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, REVIEWER_ROLES, "queue entities")
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = CreateTaskRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            task = await create_review_task(
                session,
                tenant_id=actor.tenant_id,
                entity_type=payload.entity_type,
                entity_id=payload.entity_id,
                queue_name=payload.queue_name,
                severity=payload.severity,
            )
    except ReviewNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except ReviewConflictError as exc:
        return _conflict(request, correlation_id, exc)
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(task),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/review-tasks/{task_id}/decisions")
async def decide_endpoint(task_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, REVIEWER_ROLES, "decide")
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = DecisionRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            task, decision = await decide_review_task(
                session,
                task_id,
                tenant_id=actor.tenant_id,
                reviewer_id=actor.user_id,
                decision=payload.decision,
                reason=payload.reason,
            )
    except ReviewNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except ReviewConflictError as exc:
        return _conflict(request, correlation_id, exc)
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        content=jsonable_encoder({"task": task, "decision": decision}), headers={"X-Correlation-Id": correlation_id}
    )


@router.post("/rights-records", status_code=status.HTTP_201_CREATED)
async def create_rights_endpoint(request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, RIGHTS_ROLES, "record rights")
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = RightsRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            record = await create_rights_record(
                session,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                source_id=payload.source_id,
                license_type=payload.license_type,
                allow_rebranding=payload.allow_rebranding,
                allow_public_redistribution=payload.allow_public_redistribution,
                evidence_asset_id=payload.evidence_asset_id,
                correlation_id=correlation_id,
            )
    except ReviewNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(record),
        headers={"X-Correlation-Id": correlation_id},
    )
