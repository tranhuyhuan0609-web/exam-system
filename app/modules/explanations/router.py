"""FastAPI /api/v1 routes for E04 AI Step-by-Step Explanations & SymPy Answer Checks."""

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
from app.modules.explanations.internal.explainer import UnsafeSandboxSnippetError
from app.modules.explanations.internal.service import (
    AnswerCheckRunView,
    ExplanationRunView,
    ExplanationValidationError,
    ExplanationVerificationError,
    SolutionStepInput,
    create_explanation_run,
    run_answer_check,
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

router = APIRouter(prefix="/api/v1", tags=["explanations"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class SolutionStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=30)
    body: str = Field(min_length=1, max_length=20_000)


class CreateExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_version_id: uuid.UUID
    steps: list[SolutionStepRequest] = Field(min_length=4, max_length=4)
    final_answer_text: str = Field(min_length=1, max_length=200)
    sympy_expr: str | None = Field(default=None, max_length=500)
    tolerance: float = Field(default=1e-4, ge=0.0, le=0.01)


class AnswerCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sympy_expr: str = Field(min_length=1, max_length=500)
    expected_answer: str = Field(min_length=1, max_length=200)
    tolerance: float = Field(default=1e-4, ge=0.0, le=0.01)


class ExplanationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    question_version_id: uuid.UUID
    draft_rationale_json: dict[str, Any]
    sympy_verified: bool
    status: str
    created_at: datetime
    verdict: str | None


class AnswerCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sympy_expr: str
    expected_answer: str
    sympy_value: str | None
    verdict: str
    sympy_verified: bool
    draft_save_allowed: bool
    sandbox_error: str | None
    reason: str


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


def _serialize_explanation(run: ExplanationRunView) -> ExplanationRunResponse:
    return ExplanationRunResponse(
        id=run.id,
        tenant_id=run.tenant_id,
        question_version_id=run.question_version_id,
        draft_rationale_json=run.draft_rationale_json,
        sympy_verified=run.sympy_verified,
        status=run.status,
        created_at=run.created_at,
        verdict=run.verdict,
    )


def _serialize_check(check: AnswerCheckRunView) -> AnswerCheckResponse:
    return AnswerCheckResponse(
        sympy_expr=check.sympy_expr,
        expected_answer=check.expected_answer,
        sympy_value=check.sympy_value,
        verdict=check.verdict.value,
        sympy_verified=check.sympy_verified,
        draft_save_allowed=check.draft_save_allowed,
        sandbox_error=check.sandbox_error,
        reason=check.reason,
    )


@router.post("/ai/explanations", response_model=ExplanationRunResponse, status_code=status.HTTP_201_CREATED)
async def create_explanation_endpoint(
    payload: CreateExplanationRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            run = await create_explanation_run(
                session,
                tenant_id=auth.tenant_id,
                question_version_id=payload.question_version_id,
                steps=[SolutionStepInput(kind=s.kind, body=s.body) for s in payload.steps],
                final_answer_text=payload.final_answer_text,
                sympy_expr=payload.sympy_expr,
                tolerance=payload.tolerance,
            )
    except ExplanationVerificationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={
                "verdict": exc.check.verdict.value,
                "sympy_value": exc.check.sympy_value,
                "candidate_value": exc.check.expected_answer,
                "sandbox_error": exc.check.sandbox_error,
            },
        )
    except (ExplanationValidationError, UnsafeSandboxSnippetError, ValueError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_explanation(run)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/ai/answer-checks", response_model=AnswerCheckResponse)
async def answer_check_endpoint(
    payload: AnswerCheckRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        check = run_answer_check(
            sympy_expr=payload.sympy_expr,
            expected_answer=payload.expected_answer,
            tolerance=payload.tolerance,
        )
    except (UnsafeSandboxSnippetError, ValueError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_check(check)),
        headers={"X-Correlation-ID": correlation_id},
    )
