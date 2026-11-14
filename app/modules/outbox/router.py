"""FastAPI /api/v1 admin routes for G03 transactional outbox stats and DLQ replay."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.outbox.internal.service import (
    MIN_REPLAY_REASON_LENGTH,
    DlqInvalidStateError,
    DlqReasonValidationError,
    get_outbox_stats,
    list_dlq_events,
    replay_dlq_event,
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
from app.platform.pagination import CursorError, resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1/admin", tags=["admin-outbox"])

_ADMIN_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class OutboxStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    pending: int
    published: int
    failed: int
    dlq_count: int
    oldest_pending_age_seconds: float | None


class DeadLetterItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_id: uuid.UUID
    correlation_id: str | None
    causation_id: uuid.UUID | None
    payload_json: dict[str, Any]
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    created_at: datetime


class DeadLetterListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeadLetterItemResponse]
    next_cursor: str | None
    has_more: bool
    limit: int


class DlqReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=MIN_REPLAY_REASON_LENGTH, max_length=500)


class DlqReplayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    tenant_id: uuid.UUID
    previous_status: str
    status: str
    previous_attempt_count: int
    attempt_count: int
    reason: str
    replayed_by: uuid.UUID | None
    correlation_id: str | None


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    status_override: int | None = None,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def _authenticate_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"

    settings = get_settings()
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
    except AuthTokenError as exc:
        return None, correlation_id, _problem_response(
            code=PlatformErrorCode.UNAUTHORIZED,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    normalized_roles = {role.upper() for role in actor.roles}
    if not (normalized_roles & _ADMIN_ROLES):
        return None, correlation_id, _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="admin role required to access outbox/dlq operations",
            request=request,
            correlation_id=correlation_id,
        )

    return actor, correlation_id, None


@router.get("/outbox/stats", response_model=OutboxStatsResponse)
async def get_admin_outbox_stats(
    request: Request,
    credentials: OptionalBearer = None,
) -> OutboxStatsResponse | JSONResponse:
    """Return tenant-scoped outbox delivery counts and oldest pending event age."""
    actor, correlation_id, err = _authenticate_admin(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        stats = await get_outbox_stats(session)

    return OutboxStatsResponse(
        tenant_id=actor.tenant_id,
        pending=stats.pending,
        published=stats.published,
        failed=stats.failed,
        dlq_count=stats.failed,
        oldest_pending_age_seconds=stats.oldest_pending_age_seconds,
    )


@router.get("/dlq", response_model=DeadLetterListResponse)
async def list_admin_dlq(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    credentials: OptionalBearer = None,
) -> DeadLetterListResponse | JSONResponse:
    """List FAILED outbox events (DLQ) for the authenticated tenant with signed keyset pagination."""
    actor, correlation_id, err = _authenticate_admin(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    settings = get_settings()
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            page = await list_dlq_events(
                session,
                tenant_id=actor.tenant_id,
                cursor_secret=settings.secret_key,
                limit=limit,
                cursor=cursor,
                event_type=event_type,
            )
    except CursorError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return DeadLetterListResponse(
        items=[
            DeadLetterItemResponse(
                event_id=rec.event_id,
                tenant_id=rec.tenant_id,
                event_type=rec.event_type,
                schema_version=rec.schema_version,
                aggregate_type=rec.aggregate_type,
                aggregate_id=rec.aggregate_id,
                correlation_id=rec.correlation_id,
                causation_id=rec.causation_id,
                payload_json=rec.payload_json,
                status=rec.status,
                attempt_count=rec.attempt_count,
                next_attempt_at=rec.next_attempt_at,
                created_at=rec.created_at,
            )
            for rec in page.items
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        limit=page.limit,
    )


@router.post("/dlq/{id}/replay", response_model=DlqReplayResponse)
async def replay_admin_dlq_item(
    id: uuid.UUID,
    body: DlqReplayRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> DlqReplayResponse | JSONResponse:
    """Reset a FAILED outbox event back to PENDING after validating the admin audit reason."""
    actor, correlation_id, err = _authenticate_admin(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await replay_dlq_event(
                session,
                event_id=id,
                reason=body.reason,
                actor_id=actor.user_id,
                correlation_id=correlation_id,
            )
    except DlqReasonValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except LookupError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except DlqInvalidStateError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            status_override=409,
        )

    return DlqReplayResponse(
        event_id=result.event_id,
        tenant_id=result.tenant_id,
        previous_status=result.previous_status,
        status=result.status,
        previous_attempt_count=result.previous_attempt_count,
        attempt_count=result.attempt_count,
        reason=result.reason,
        replayed_by=result.replayed_by,
        correlation_id=result.correlation_id,
    )
