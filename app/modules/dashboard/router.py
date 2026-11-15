"""FastAPI /api/v1 routes for D01 Learner Dashboard & Activity."""

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
from app.modules.dashboard.internal.service import (
    ActivityEventView,
    DashboardValidationError,
    StoredDashboardProjectionView,
    get_learner_dashboard,
    list_activity_events,
    record_activity_event,
)
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

router = APIRouter(prefix="/api/v1/learners/me", tags=["dashboard"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class RecordActivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: str = Field(min_length=1, max_length=50)
    entity_id: uuid.UUID
    duration_seconds: int = Field(default=0, ge=0, le=86_400)
    occurred_at: datetime | None = None


class ActivityEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    activity_type: str
    entity_id: uuid.UUID
    duration_seconds: int
    occurred_at: datetime


class DashboardProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    streak_days: int
    summary_json: dict[str, Any]
    updated_at: datetime


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        status_override=status_code,
        instance=instance,
        correlation_id=correlation_id,
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
) -> ActorContext | JSONResponse:
    settings = get_settings()
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    if not raw_auth and request.cookies.get("ulp_access_token"):
        raw_auth = f"Bearer {request.cookies['ulp_access_token']}"
    try:
        token = extract_bearer_token(raw_auth)
        actor = decode_and_verify_actor_token(
            token,
            secret=settings.secret_key,
            now_epoch=int(time.time()),
        )
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
        return actor
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


def _serialize_activity(row: ActivityEventView) -> ActivityEventResponse:
    return ActivityEventResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        activity_type=row.activity_type,
        entity_id=row.entity_id,
        duration_seconds=row.duration_seconds,
        occurred_at=row.occurred_at,
    )


def _serialize_dashboard(row: StoredDashboardProjectionView) -> DashboardProjectionResponse:
    return DashboardProjectionResponse(
        membership_id=row.membership_id,
        tenant_id=row.tenant_id,
        streak_days=row.streak_days,
        summary_json=row.summary_json,
        updated_at=row.updated_at,
    )


@router.get("/dashboard", response_model=DashboardProjectionResponse)
async def get_dashboard_endpoint(
    request: Request,
    refresh: Annotated[bool, Query()] = False,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            projection = await get_learner_dashboard(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                refresh=refresh,
            )
    except DashboardValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_dashboard(projection)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/activity", response_model=list[ActivityEventResponse])
async def list_activity_endpoint(
    request: Request,
    activity_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            rows = await list_activity_events(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                activity_type=activity_type,
                limit=limit,
            )
    except DashboardValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder([_serialize_activity(r) for r in rows]),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/activity", response_model=ActivityEventResponse, status_code=status.HTTP_201_CREATED)
async def record_activity_endpoint(
    payload: RecordActivityRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            created = await record_activity_event(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                activity_type=payload.activity_type,
                entity_id=payload.entity_id,
                duration_seconds=payload.duration_seconds,
                occurred_at=payload.occurred_at,
            )
    except DashboardValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_activity(created)),
        headers={"X-Correlation-ID": correlation_id},
    )
