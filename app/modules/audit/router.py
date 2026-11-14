"""FastAPI /api/v1 routes for G02: the tenant's audit log and legal takedown cases.

The tenant comes only from the signed JWT. Reading the log, opening and executing a takedown are
administrator actions; another tenant's case or target answers 404.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.audit.internal.diff import MIN_SENSITIVE_REASON_LEN
from app.modules.audit.internal.service import (
    AuditEventRecord,
    AuditNotFoundError,
    TakedownCaseRecord,
    TakedownConflictError,
    UnknownActorError,
    create_takedown_case,
    execute_takedown_case,
    list_audit_events,
)
from app.modules.privacy.public import bind_user
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

router = APIRouter(prefix="/api/v1", tags=["audit"])

ADMIN_ROLES = frozenset({"ADMIN", "TENANT_ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID
    reason: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    correlation_id: str | None
    occurred_at: datetime


class AuditLogPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEventResponse]
    next_cursor: str | None
    has_more: bool
    limit: int


class CreateTakedownRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complainant_name: str = Field(min_length=1, max_length=255)
    target_entity_type: Literal["SOURCE_DOCUMENT", "CONTENT_ITEM", "QUESTION"]
    target_entity_id: uuid.UUID
    # The audit trail requires a substantive reason for takedown actions.
    legal_reason: str = Field(min_length=MIN_SENSITIVE_REASON_LEN, max_length=5000)


class TakedownCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    complainant_name: str
    target_entity_type: str
    target_entity_id: uuid.UUID
    legal_reason: str
    status: str
    executed_by: uuid.UUID | None
    executed_at: datetime | None
    created_at: datetime


class TakedownExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: TakedownCaseResponse
    audit_event_id: uuid.UUID
    event_id: uuid.UUID


def _event_response(rec: AuditEventRecord) -> AuditEventResponse:
    return AuditEventResponse(**{f: getattr(rec, f) for f in AuditEventResponse.model_fields})


def _case_response(rec: TakedownCaseRecord) -> TakedownCaseResponse:
    return TakedownCaseResponse(**{f: getattr(rec, f) for f in TakedownCaseResponse.model_fields})


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
    request: Request, credentials: HTTPAuthorizationCredentials | None
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
    if not ({r.upper() for r in actor.roles} & ADMIN_ROLES):
        return (
            None,
            correlation_id,
            _problem(PlatformErrorCode.FORBIDDEN, "administrator role required", request, correlation_id),
        )
    return actor, correlation_id, None


@router.get("/audit-logs", response_model=AuditLogPage)
async def get_audit_logs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    target_type: str | None = Query(default=None, max_length=60),
    target_id: Annotated[uuid.UUID | None, Query()] = None,
    action: str | None = Query(default=None, max_length=100),
    credentials: OptionalBearer = None,
) -> Any:
    """The caller's tenant audit trail, newest first (signed keyset pagination)."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            page = await list_audit_events(
                session,
                tenant_id=actor.tenant_id,
                cursor_secret=get_settings().secret_key,
                limit=limit,
                cursor=cursor,
                target_type=target_type,
                target_id=target_id,
                action=action,
            )
    except CursorError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    return AuditLogPage(
        items=[_event_response(r) for r in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        limit=page.limit,
    )


@router.post("/takedown-cases", response_model=TakedownCaseResponse, status_code=status.HTTP_201_CREATED)
async def post_takedown_case(body: CreateTakedownRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Open a legal takedown case against a target of the caller's tenant."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            case = await create_takedown_case(
                session,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                complainant_name=body.complainant_name,
                target_entity_type=body.target_entity_type,
                target_entity_id=body.target_entity_id,
                legal_reason=body.legal_reason,
                correlation_id=correlation_id,
            )
    except AuditNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    return _case_response(case)


@router.post("/takedown-cases/{case_id}/execute", response_model=TakedownExecutionResponse)
async def post_execute_takedown(case_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """Execute an OPEN case: target taken down, case closed, audit event and outbox event, in one transaction."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            await bind_user(session, actor.user_id)
            execution = await execute_takedown_case(
                session, case_id, executor_id=actor.user_id, correlation_id=correlation_id
            )
    except AuditNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except UnknownActorError as exc:
        return _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    except TakedownConflictError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    return TakedownExecutionResponse(
        case=_case_response(execution.case),
        audit_event_id=execution.audit_event.id,
        event_id=execution.event_id,
    )
