"""FastAPI /api/v1 routes for E02 AI Tutor & Grounded Q&A (GATE-08 Exam Lock)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.tutor.internal.guard import TutorGuardError, TutorMode
from app.modules.tutor.internal.service import (
    TutorMessageView,
    TutorNotFoundError,
    TutorSessionView,
    TutorTurnView,
    TutorValidationError,
    create_tutor_session,
    send_tutor_message,
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

router = APIRouter(prefix="/api/v1", tags=["tutor"])



class CreateTutorSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tutor_mode: str = Field(default=TutorMode.HINT_MODE.value, max_length=30)
    context_question_version_id: uuid.UUID | None = None
    context_content_version_id: uuid.UUID | None = None
    target_attempt_id: uuid.UUID | None = None


class SendTutorMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_md: str = Field(min_length=1, max_length=4000)
    target_attempt_id: uuid.UUID | None = None


class TutorSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    context_question_version_id: uuid.UUID | None
    context_content_version_id: uuid.UUID | None
    tutor_mode: str
    created_at: datetime


class TutorMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tutor_session_id: uuid.UUID
    tenant_id: uuid.UUID
    sender_role: str
    message_md: str
    citations_json: list[dict[str, Any]]
    created_at: datetime


class TutorTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: TutorSessionResponse
    user_message: TutorMessageResponse
    assistant_message: TutorMessageResponse
    retrieval_trace_id: uuid.UUID
    retrieved_ids: list[uuid.UUID]
    scores: list[Decimal]
    blocked_injection: bool
    blocked_leakage: bool
    guard_reason_code: str


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


def _map_guard_error(
    exc: TutorGuardError,
    *,
    instance: str,
    correlation_id: str,
) -> JSONResponse:
    platform_code = (
        PlatformErrorCode.EXAM_SESSION_ACTIVE
        if exc.code == "EXAM_SESSION_ACTIVE"
        else PlatformErrorCode.FORBIDDEN
    )
    return _problem_response(
        code=platform_code,
        detail=str(exc),
        status_code=403,
        instance=instance,
        correlation_id=correlation_id,
        extensions={"guard_code": exc.code},
    )


def _serialize_session(s: TutorSessionView) -> TutorSessionResponse:
    return TutorSessionResponse(
        id=s.id,
        tenant_id=s.tenant_id,
        membership_id=s.membership_id,
        context_question_version_id=s.context_question_version_id,
        context_content_version_id=s.context_content_version_id,
        tutor_mode=s.tutor_mode.value,
        created_at=s.created_at,
    )


def _serialize_message(m: TutorMessageView) -> TutorMessageResponse:
    return TutorMessageResponse(
        id=m.id,
        tutor_session_id=m.tutor_session_id,
        tenant_id=m.tenant_id,
        sender_role=m.sender_role.value,
        message_md=m.message_md,
        citations_json=list(m.citations_json),
        created_at=m.created_at,
    )


def _serialize_turn(turn: TutorTurnView) -> TutorTurnResponse:
    return TutorTurnResponse(
        session=_serialize_session(turn.session),
        user_message=_serialize_message(turn.user_message),
        assistant_message=_serialize_message(turn.assistant_message),
        retrieval_trace_id=turn.retrieval_trace_id,
        retrieved_ids=list(turn.retrieved_ids),
        scores=list(turn.scores),
        blocked_injection=turn.blocked_injection,
        blocked_leakage=turn.blocked_leakage,
        guard_reason_code=turn.guard_reason_code,
    )


@router.post("/tutor/sessions", response_model=TutorSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_tutor_session_endpoint(
    payload: CreateTutorSessionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            tutor_session = await create_tutor_session(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                tutor_mode=payload.tutor_mode,
                context_question_version_id=payload.context_question_version_id,
                context_content_version_id=payload.context_content_version_id,
                target_attempt_id=payload.target_attempt_id,
            )
    except TutorGuardError as exc:
        return _map_guard_error(exc, instance=str(request.url.path), correlation_id=correlation_id)
    except TutorValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_session(tutor_session)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post(
    "/tutor/sessions/{session_id}/messages",
    response_model=TutorTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_tutor_message_endpoint(
    session_id: uuid.UUID,
    payload: SendTutorMessageRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            turn = await send_tutor_message(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                tutor_session_id=session_id,
                message_md=payload.message_md,
                target_attempt_id=payload.target_attempt_id,
            )
    except TutorNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except TutorGuardError as exc:
        return _map_guard_error(exc, instance=str(request.url.path), correlation_id=correlation_id)
    except TutorValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_turn(turn)),
        headers={"X-Correlation-ID": correlation_id},
    )
