"""FastAPI /api/v1 routes for E05 Explainable Next-Step Recommendations."""

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
from app.modules.recommendations.internal.service import (
    RecommendationNotFoundError,
    RecommendationValidationError,
    RecommendationView,
    list_learner_recommendations,
    submit_recommendation_feedback,
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

router = APIRouter(prefix="/api/v1", tags=["recommendations"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class RecommendationFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=500)


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    recommended_action_type: str
    target_entity_id: uuid.UUID
    title: str
    expected_gain_score: float
    evidence_json: dict[str, Any]
    status: str
    feedback: str | None
    hidden: bool
    created_at: datetime


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


def _serialize(view: RecommendationView) -> RecommendationResponse:
    return RecommendationResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        membership_id=view.membership_id,
        recommended_action_type=view.recommended_action_type,
        target_entity_id=view.target_entity_id,
        title=view.title,
        expected_gain_score=view.expected_gain_score,
        evidence_json=view.evidence_json,
        status=view.status,
        feedback=view.feedback,
        hidden=view.hidden,
        created_at=view.created_at,
    )


@router.get("/recommendations")
async def get_recommendations_endpoint(
    request: Request,
    top_k: int = 3,
    refresh: bool = False,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            items = await list_learner_recommendations(
                session,
                tenant_id=actor.tenant_id,
                membership_id=actor.membership_id,
                top_k=top_k,
                refresh=refresh,
            )
    except RecommendationValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"error_code": exc.code},
        )
    except RecommendationNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"items": [jsonable_encoder(_serialize(item)) for item in items]},
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/recommendations/{recommendation_id}/feedback")
async def submit_recommendation_feedback_endpoint(
    recommendation_id: uuid.UUID,
    payload: RecommendationFeedbackRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            updated = await submit_recommendation_feedback(
                session,
                tenant_id=actor.tenant_id,
                membership_id=actor.membership_id,
                recommendation_id=recommendation_id,
                feedback=payload.feedback,
                note=payload.note,
            )
    except RecommendationValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"error_code": exc.code},
        )
    except RecommendationNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize(updated)),
        headers={"X-Correlation-ID": correlation_id},
    )
