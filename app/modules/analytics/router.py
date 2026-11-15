"""FastAPI /api/v1 routes for G04 Learning & Operational Analytics Warehouse."""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.analytics.internal.service import (
    AnalyticsValidationError,
    PipelineYieldDayView,
    PlatformKpisView,
    get_pipeline_yield,
    get_platform_kpis,
    record_analytics_event,
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

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "REVIEWER"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class RecordAnalyticsEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1, max_length=80)
    learner_id: uuid.UUID | None = None
    count: int = Field(default=1, ge=0)
    pre_mastery: float | None = None
    post_mastery: float | None = None
    event_id: str | None = Field(default=None, max_length=120)
    occurred_at: datetime | None = None
    extra_dimensions: dict[str, Any] | None = None


class PlatformKpisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    metric_date: date | None
    dau_count: int
    wlau_count: int
    extracted_total: int
    auto_verified_total: int
    manual_edited_total: int
    pipeline_yield_rate: float
    manual_edit_rate: float
    avg_mastery_gain: float


class PipelineYieldDayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    metric_date: date
    extracted_total: int
    auto_verified_total: int
    manual_edited_total: int
    pipeline_yield_rate: float
    manual_edit_rate: float


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
    return actor


def _serialize_kpis(view: PlatformKpisView) -> PlatformKpisResponse:
    return PlatformKpisResponse(
        tenant_id=view.tenant_id,
        metric_date=view.metric_date,
        dau_count=view.dau_count,
        wlau_count=view.wlau_count,
        extracted_total=view.extracted_total,
        auto_verified_total=view.auto_verified_total,
        manual_edited_total=view.manual_edited_total,
        pipeline_yield_rate=view.pipeline_yield_rate,
        manual_edit_rate=view.manual_edit_rate,
        avg_mastery_gain=view.avg_mastery_gain,
    )


def _serialize_yield_day(view: PipelineYieldDayView) -> PipelineYieldDayResponse:
    return PipelineYieldDayResponse(
        tenant_id=view.tenant_id,
        metric_date=view.metric_date,
        extracted_total=view.extracted_total,
        auto_verified_total=view.auto_verified_total,
        manual_edited_total=view.manual_edited_total,
        pipeline_yield_rate=view.pipeline_yield_rate,
        manual_edit_rate=view.manual_edit_rate,
    )


@router.post("/events")
async def record_analytics_event_endpoint(
    payload: RecordAnalyticsEventRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            created = await record_analytics_event(
                session,
                tenant_id=actor.tenant_id,
                event_type=payload.event_type,
                learner_id=payload.learner_id,
                count=payload.count,
                pre_mastery=payload.pre_mastery,
                post_mastery=payload.post_mastery,
                event_id=payload.event_id,
                occurred_at=payload.occurred_at,
                extra_dimensions=payload.extra_dimensions,
            )
    except AnalyticsValidationError as exc:
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
        content=jsonable_encoder(created),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/platform-kpis")
async def get_platform_kpis_endpoint(
    request: Request,
    metric_date: date | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    if not any(r.upper() in _STAFF_ROLES for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff role is required to query platform analytics KPIs",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    async with tenant_transaction(actor.tenant_id, role=DbRole.ANALYTICS_RO) as session:
        kpis = await get_platform_kpis(
            session,
            tenant_id=actor.tenant_id,
            metric_date=metric_date,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_kpis(kpis)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/pipeline-yield")
async def get_pipeline_yield_endpoint(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    if not any(r.upper() in _STAFF_ROLES for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff role is required to query pipeline yield metrics",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.ANALYTICS_RO) as session:
            days = await get_pipeline_yield(
                session,
                tenant_id=actor.tenant_id,
                start_date=start_date,
                end_date=end_date,
            )
    except AnalyticsValidationError as exc:
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
        content={"items": [jsonable_encoder(_serialize_yield_day(d)) for d in days]},
        headers={"X-Correlation-ID": correlation_id},
    )
