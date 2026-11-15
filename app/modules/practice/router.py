"""FastAPI /api/v1 routes for D03 Interactive Practice Engine."""

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
from app.modules.practice.internal.service import (
    PracticeCompletionView,
    PracticeDomainError,
    PracticeNotFoundError,
    PracticeResponseView,
    PracticeSessionView,
    PracticeValidationError,
    complete_session,
    get_practice_session,
    reveal_practice_hint,
    start_practice_session,
    submit_practice_answer,
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

router = APIRouter(prefix="/api/v1/practice-sessions", tags=["practice"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class StartPracticeSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="LESSON_EMBED", min_length=1, max_length=30)
    question_version_ids: list[uuid.UUID] = Field(min_length=1)
    immediate_feedback: bool = True


class RevealPracticeHintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_version_id: uuid.UUID


class SubmitPracticeResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_version_id: uuid.UUID
    response_id: str = Field(min_length=1, max_length=120)
    submitted_answer: str = Field(min_length=1, max_length=4000)
    hints_used: int | None = Field(default=None, ge=0)
    immediate_feedback: bool | None = None


class PracticeResponseItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    practice_session_id: uuid.UUID
    question_version_id: uuid.UUID
    response_id: str
    submitted_answer: str
    is_correct: bool | None
    hints_used: int
    immediate_feedback: bool
    explanation_md: str | None
    answered_at: datetime


class PracticeSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    mode: str
    status: str
    immediate_feedback: bool
    created_at: datetime
    completed_at: datetime | None
    responses: list[PracticeResponseItemModel]


class RevealHintResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    question_version_id: uuid.UUID
    hint_text: str | None
    hints_used: int


class MasterySignalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_id: str
    question_version_id: str
    is_correct: bool
    hints_used: int
    source_kind: str


class CompletePracticeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: PracticeSessionResponse
    total_answered: int
    correct_count: int
    accuracy: float
    error_note_question_ids: list[str]
    resolved_remediation_question_ids: list[str]
    mastery_signals: list[MasterySignalItem]
    mastery_summary: dict[str, dict[str, int]]


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


def _serialize_response(resp: PracticeResponseView) -> PracticeResponseItemModel:
    return PracticeResponseItemModel(
        id=resp.id,
        tenant_id=resp.tenant_id,
        practice_session_id=resp.practice_session_id,
        question_version_id=resp.question_version_id,
        response_id=resp.response_id,
        submitted_answer=resp.submitted_answer,
        is_correct=resp.is_correct,
        hints_used=resp.hints_used,
        immediate_feedback=resp.immediate_feedback,
        explanation_md=resp.explanation_md,
        answered_at=resp.answered_at,
    )


def _serialize_session(sess: PracticeSessionView) -> PracticeSessionResponse:
    return PracticeSessionResponse(
        id=sess.id,
        tenant_id=sess.tenant_id,
        membership_id=sess.membership_id,
        mode=sess.mode,
        status=sess.status,
        immediate_feedback=sess.immediate_feedback,
        created_at=sess.created_at,
        completed_at=sess.completed_at,
        responses=[_serialize_response(r) for r in sess.responses],
    )


def _serialize_completion(report: PracticeCompletionView) -> CompletePracticeResponse:
    return CompletePracticeResponse(
        session=_serialize_session(report.session),
        total_answered=report.total_answered,
        correct_count=report.correct_count,
        accuracy=report.accuracy,
        error_note_question_ids=list(report.error_note_question_ids),
        resolved_remediation_question_ids=list(report.resolved_remediation_question_ids),
        mastery_signals=[
            MasterySignalItem(
                taxonomy_node_id=sig.taxonomy_node_id or "UNSPECIFIED",
                question_version_id=sig.question_version_id,
                is_correct=sig.is_correct,
                hints_used=sig.hints_used,
                source_kind=sig.source_kind,
            )
            for sig in report.mastery_signals
        ],
        mastery_summary={k: dict(v) for k, v in report.mastery_summary.items()},
    )


def _handle_domain_error(
    exc: PracticeDomainError,
    request: Request,
    correlation_id: str,
) -> JSONResponse:
    code = PlatformErrorCode.NOT_FOUND if exc.status_code == 404 else PlatformErrorCode.VALIDATION_ERROR
    return _problem_response(
        code=code,
        detail=exc.detail,
        status_code=exc.status_code,
        instance=str(request.url.path),
        correlation_id=correlation_id,
        extensions={"domain_code": exc.code},
    )


@router.post("", response_model=PracticeSessionResponse, status_code=status.HTTP_201_CREATED)
async def start_practice_endpoint(
    payload: StartPracticeSessionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            created = await start_practice_session(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                mode=payload.mode,
                question_version_ids=payload.question_version_ids,
                immediate_feedback=payload.immediate_feedback,
            )
    except PracticeNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except PracticeValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_session(created)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{session_id}", response_model=PracticeSessionResponse)
async def get_practice_endpoint(
    session_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            sess = await get_practice_session(
                session,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                membership_id=auth.membership_id,
            )
    except PracticeNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_session(sess)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{session_id}/hints", response_model=RevealHintResponse)
async def reveal_hint_endpoint(
    session_id: uuid.UUID,
    payload: RevealPracticeHintRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            hint_text, hints_used = await reveal_practice_hint(
                session,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                question_version_id=payload.question_version_id,
                membership_id=auth.membership_id,
            )
    except PracticeNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except PracticeDomainError as exc:
        return _handle_domain_error(exc, request, correlation_id)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(
            RevealHintResponse(
                session_id=session_id,
                question_version_id=payload.question_version_id,
                hint_text=hint_text,
                hints_used=hints_used,
            )
        ),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{session_id}/responses", response_model=PracticeResponseItemModel)
async def submit_response_endpoint(
    session_id: uuid.UUID,
    payload: SubmitPracticeResponseRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            recorded = await submit_practice_answer(
                session,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                question_version_id=payload.question_version_id,
                response_id=payload.response_id,
                submitted_answer=payload.submitted_answer,
                membership_id=auth.membership_id,
                hints_used=payload.hints_used,
                immediate_feedback=payload.immediate_feedback,
            )
    except PracticeNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except PracticeDomainError as exc:
        return _handle_domain_error(exc, request, correlation_id)
    except PracticeValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_response(recorded)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{session_id}/complete", response_model=CompletePracticeResponse)
async def complete_practice_endpoint(
    session_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            report = await complete_session(
                session,
                tenant_id=auth.tenant_id,
                session_id=session_id,
                membership_id=auth.membership_id,
            )
    except PracticeNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except PracticeDomainError as exc:
        return _handle_domain_error(exc, request, correlation_id)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_completion(report)),
        headers={"X-Correlation-ID": correlation_id},
    )
