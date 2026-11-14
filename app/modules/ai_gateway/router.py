"""FastAPI /api/v1/ai routes for E01: submit an AI job, read it, read the tenant's usage and budget.

The tenant comes only from the signed JWT. A job is readable by the person who asked for it and by tenant
administrators; usage is for administrators. A tenant at its daily ceiling gets 429 DAILY_AI_BUDGET_EXCEEDED.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.ai_gateway.internal.budget import DailyBudgetExceededError
from app.modules.ai_gateway.internal.service import (
    GATEWAY_KEY,
    JobRecord,
    NoModelAvailableError,
    PromptNotFoundError,
    get_job,
    submit_job,
    usage_report,
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

router = APIRouter(prefix="/api/v1/ai", tags=["ai-gateway"])

ADMIN_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})
MAX_ESTIMATED_TOKENS = 10_000_000
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class SubmitJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(min_length=1, max_length=50, pattern=r"^[A-Z][A-Z0-9_]*$")
    task_role: Literal["OCR_VISION", "HEALER_FAST", "VERIFIER_REASONING", "TUTOR_CHAT"] | None = None
    prompt_code: str | None = Field(default=None, min_length=1, max_length=80)
    input_refs: dict[str, Any] = Field(default_factory=dict)
    estimated_prompt_tokens: int = Field(default=0, ge=0, le=MAX_ESTIMATED_TOKENS)
    estimated_completion_tokens: int = Field(default=0, ge=0, le=MAX_ESTIMATED_TOKENS)


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    task_type: str
    status: str
    task_role: str | None
    prompt_version_id: uuid.UUID | None
    input_refs: dict[str, Any]
    output: dict[str, Any] | None
    estimated_cost_usd: Decimal
    primary_model_id: uuid.UUID | None
    fallback_model_ids: list[uuid.UUID]
    created_at: datetime


class ModelUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: uuid.UUID
    provider_code: str
    model_name: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal


class UsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    vn_day: date
    daily_ai_budget_usd: Decimal
    spent_usd: Decimal
    reserved_usd: Decimal
    remaining_usd: Decimal
    calls: int
    by_model: list[ModelUsageResponse]


def _job_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        task_type=job.task_type,
        status=job.status,
        task_role=job.task_role,
        prompt_version_id=job.prompt_version_id,
        input_refs=job.input_refs,
        output=job.output,
        estimated_cost_usd=job.estimated_cost_usd,
        primary_model_id=job.primary_model_id,
        fallback_model_ids=list(job.fallback_model_ids),
        created_at=job.created_at,
    )


def _problem(
    code: PlatformErrorCode, detail: str, request: Request, correlation_id: str, status_override: int | None = None
) -> JSONResponse:
    built = build_problem_detail(
        code=code, detail=detail, instance=request.url.path, correlation_id=correlation_id,
        status_override=status_override,
    )  # fmt: skip
    return JSONResponse(
        status_code=built.status,
        content=built.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, allowed_roles: frozenset[str] | None = None
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    try:
        actor = decode_and_verify_actor_token(
            extract_bearer_token(raw_auth), secret=get_settings().secret_key, now_epoch=int(time.time())
        )
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if allowed_roles is not None and not ({r.upper() for r in actor.roles} & allowed_roles):
        return (
            None,
            correlation_id,
            _problem(PlatformErrorCode.FORBIDDEN, "administrator role required", request, correlation_id),
        )
    return actor, correlation_id, None


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def post_ai_job(body: SubmitJobRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Queue an AI job. Refused with 429 when the tenant's daily budget is spent or would be exceeded."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    if GATEWAY_KEY in body.input_refs:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR, f"input_refs must not contain {GATEWAY_KEY!r}", request, correlation_id
        )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            job = await submit_job(
                session,
                tenant_id=actor.tenant_id,
                requested_by=actor.user_id,
                task_type=body.task_type,
                input_refs=body.input_refs,
                prompt_code=body.prompt_code,
                task_role=body.task_role,
                estimated_prompt_tokens=body.estimated_prompt_tokens,
                estimated_completion_tokens=body.estimated_completion_tokens,
            )
    except DailyBudgetExceededError as exc:
        return _problem(PlatformErrorCode.DAILY_AI_BUDGET_EXCEEDED, str(exc), request, correlation_id)
    except PromptNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except NoModelAvailableError as exc:
        return _problem(PlatformErrorCode.INTERNAL_ERROR, str(exc), request, correlation_id, status_override=503)
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_ai_job(job_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """A job of the caller's tenant: readable by whoever asked for it and by tenant administrators."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        job = await get_job(session, job_id)
    is_admin = bool({r.upper() for r in actor.roles} & ADMIN_ROLES)
    if job is None or (job.requested_by != actor.user_id and not is_admin):
        return _problem(PlatformErrorCode.NOT_FOUND, "job not found", request, correlation_id)
    return _job_response(job)


@router.get("/usage", response_model=UsageResponse)
async def get_ai_usage(
    request: Request,
    day: Annotated[date | None, Query(description="Vietnamese calendar day; default today")] = None,
    credentials: OptionalBearer = None,
) -> Any:
    """The tenant's budget position for a Vietnamese day and the calls per model (administrators)."""
    actor, _, err = _authenticate(request, credentials, ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        report = await usage_report(session, actor.tenant_id, day=day)
    b = report.budget
    return UsageResponse(
        tenant_id=actor.tenant_id,
        vn_day=b.vn_day,
        daily_ai_budget_usd=b.daily_ai_budget_usd,
        spent_usd=b.spent_usd,
        reserved_usd=b.reserved_usd,
        remaining_usd=b.remaining_usd,
        calls=report.calls,
        by_model=[
            ModelUsageResponse(**{f: getattr(m, f) for f in ModelUsageResponse.model_fields}) for m in report.by_model
        ],
    )
