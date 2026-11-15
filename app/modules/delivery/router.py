"""FastAPI /api/v1 routes for C05 delivery: start an exam session, read the paper, submit.

The tenant and the membership come only from the signed JWT. The routes run as `ulp_exam_session_rw`, a database
role with no grant on any answer table: the paper they serve cannot contain a key, whatever a bug might try.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.contracts.delivery import SanitizedExamPaperContract
from app.modules.delivery.internal.service import (
    AttemptRecord,
    DeliveryConflictError,
    DeliveryForbiddenError,
    DeliveryNotFoundError,
    get_attempt_paper,
    start_exam_session,
    submit_attempt,
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

router = APIRouter(prefix="/api/v1", tags=["delivery"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=64)]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: uuid.UUID


class AttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: uuid.UUID
    assessment_version_id: uuid.UUID
    status: str
    started_at: datetime
    deadline_at: datetime
    submitted_at: datetime | None


class StartResponse(AttemptResponse):
    duration_seconds: int
    resumed: bool


class SubmitResponse(AttemptResponse):
    replayed: bool


def _attempt_values(record: AttemptRecord) -> dict[str, Any]:
    return {
        "attempt_id": record.id,
        "assessment_version_id": record.assessment_version_id,
        "status": record.status,
        "started_at": record.started_at,
        "deadline_at": record.deadline_at,
        "submitted_at": record.submitted_at,
    }


def _problem(
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_dict(),
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
    return actor, correlation_id, None


def _service_error(exc: Exception, request: Request, correlation_id: str) -> JSONResponse:
    if isinstance(exc, DeliveryNotFoundError):
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    if isinstance(exc, DeliveryForbiddenError):
        return _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id, extensions={"reason": exc.code})
    if isinstance(exc, DeliveryConflictError):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            request,
            correlation_id,
            status_override=status.HTTP_409_CONFLICT,
            extensions={"reason": exc.code},
        )
    return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)


@router.post("/exam-sessions/start")
async def post_start(body: StartRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Start the caller's attempt at a published paper, or return the one already in progress (201 / 200)."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.EXAM_SESSION_RW) as session:
            result = await start_exam_session(
                session,
                tenant_id=actor.tenant_id,
                membership_id=actor.membership_id,
                assessment_id=body.assessment_id,
                correlation_id=correlation_id,
            )
    except (DeliveryNotFoundError, DeliveryForbiddenError, DeliveryConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    payload = StartResponse(
        **_attempt_values(result.attempt), duration_seconds=result.duration_seconds, resumed=result.resumed
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if result.resumed else status.HTTP_201_CREATED,
        content=jsonable_encoder(payload),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/attempts/{attempt_id}/paper", response_model=SanitizedExamPaperContract)
async def get_paper(attempt_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """The caller's paper in its stored order, answer-free; same bytes on every reload and device."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.EXAM_SESSION_RW) as session:
            paper = await get_attempt_paper(session, attempt_id, membership_id=actor.membership_id)
    except (DeliveryNotFoundError, DeliveryForbiddenError, DeliveryConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    return paper


@router.post("/attempts/{attempt_id}/submit", response_model=SubmitResponse)
async def post_submit(
    attempt_id: uuid.UUID,
    request: Request,
    idempotency_key: IdempotencyKey = None,
    credentials: OptionalBearer = None,
) -> Any:
    """Submit the attempt; a repeated `Idempotency-Key` replays the first answer."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.EXAM_SESSION_RW) as session:
            result = await submit_attempt(
                session,
                attempt_id,
                membership_id=actor.membership_id,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
    except (DeliveryNotFoundError, DeliveryForbiddenError, DeliveryConflictError) as exc:
        return _service_error(exc, request, correlation_id)
    return SubmitResponse(**_attempt_values(result.attempt), replayed=result.replayed)
