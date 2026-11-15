"""FastAPI /api/v1/ai/generations routes for E03 AI Question Generation."""

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
from app.contracts.enums import PublicationStatus, QuestionItemType, RightsStatus
from app.modules.generation.internal.service import (
    AnchorInput,
    GenerationNotFoundError,
    GenerationPolicyViolationError,
    GenerationPublishView,
    GenerationRequestView,
    GenerationValidationError,
    approve_generation_and_publish,
    create_ai_generation,
    get_generation_request,
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

router = APIRouter(prefix="/api/v1/ai/generations", tags=["generation"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "REVIEWER"})
_REVIEWER_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "REVIEWER"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class AnchorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1, max_length=120)
    document_version_id: uuid.UUID
    excerpt: str = Field(min_length=1)
    publication_status: PublicationStatus = PublicationStatus.PUBLISHED
    rights_status: RightsStatus = RightsStatus.OWNED
    owner_tenant_id: uuid.UUID | None = None
    source_question_id: uuid.UUID | None = None


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_id: uuid.UUID
    target_difficulty: str = Field(default="VD", min_length=1, max_length=20)
    question_type: QuestionItemType = QuestionItemType.SHORT_NUMERIC
    missing_count: int = Field(default=1, ge=1)
    prompt_version: str = Field(default="e03_v1", min_length=1, max_length=80)
    anchors: list[AnchorPayload] = Field(default_factory=list)
    draft_stem: str = Field(min_length=1)
    computed_expression: str | None = None
    source_answer: str | None = None
    verify_with_c02: bool = True
    requested_publication_status: str | None = None
    skip_review: bool = False


class ApproveGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="Human reviewer approved C02-verified AI question", min_length=1, max_length=500)


class GenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    requested_by: uuid.UUID
    taxonomy_node_id: uuid.UUID
    target_difficulty: str
    status: str
    generated_candidate_id: uuid.UUID | None
    acquisition_method: str | None
    verification_status: str | None
    candidate_status: str | None
    publication_status: str
    review_task_id: uuid.UUID | None
    source_anchors_json: list[dict[str, Any]]
    created_at: datetime


class GenerationPublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_request_id: uuid.UUID
    candidate_id: uuid.UUID
    review_task_id: uuid.UUID
    review_decision_id: uuid.UUID
    question_id: uuid.UUID
    question_version_id: uuid.UUID
    acquisition_method: str
    verification_status: str
    publication_status: str
    status: str


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
    *,
    allowed_roles: frozenset[str] = _STAFF_ROLES,
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
    if not any(r.upper() in allowed_roles for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Insufficient role for AI generation workflow",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_generation(view: GenerationRequestView) -> GenerationResponse:
    return GenerationResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        requested_by=view.requested_by,
        taxonomy_node_id=view.taxonomy_node_id,
        target_difficulty=view.target_difficulty,
        status=view.status,
        generated_candidate_id=view.generated_candidate_id,
        acquisition_method=view.acquisition_method,
        verification_status=view.verification_status,
        candidate_status=view.candidate_status,
        publication_status=view.publication_status,
        review_task_id=view.review_task_id,
        source_anchors_json=view.source_anchors_json,
        created_at=view.created_at,
    )


def _serialize_publish(view: GenerationPublishView) -> GenerationPublishResponse:
    return GenerationPublishResponse(
        generation_request_id=view.generation_request_id,
        candidate_id=view.candidate_id,
        review_task_id=view.review_task_id,
        review_decision_id=view.review_decision_id,
        question_id=view.question_id,
        question_version_id=view.question_version_id,
        acquisition_method=view.acquisition_method,
        verification_status=view.verification_status,
        publication_status=view.publication_status,
        status=view.status,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_generation_endpoint(
    payload: CreateGenerationRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    anchors = [
        AnchorInput(
            anchor_id=a.anchor_id,
            document_version_id=a.document_version_id,
            excerpt=a.excerpt,
            publication_status=a.publication_status,
            rights_status=a.rights_status,
            owner_tenant_id=a.owner_tenant_id,
            source_question_id=a.source_question_id,
        )
        for a in payload.anchors
    ]
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await create_ai_generation(
                session,
                tenant_id=actor.tenant_id,
                requested_by=actor.user_id,
                taxonomy_node_id=payload.taxonomy_node_id,
                target_difficulty=payload.target_difficulty,
                question_type=payload.question_type,
                missing_count=payload.missing_count,
                prompt_version=payload.prompt_version,
                anchors=anchors,
                draft_stem=payload.draft_stem,
                computed_expression=payload.computed_expression,
                source_answer=payload.source_answer,
                verify_with_c02=payload.verify_with_c02,
                requested_publication_status=payload.requested_publication_status,
                skip_review=payload.skip_review,
            )
    except GenerationNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except GenerationPolicyViolationError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )
    except GenerationValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"validation_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_generation(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{request_id}")
async def get_generation_endpoint(
    request_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await get_generation_request(session, tenant_id=actor.tenant_id, request_id=request_id)
    except GenerationNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_generation(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{request_id}/approve")
async def approve_generation_endpoint(
    request_id: uuid.UUID,
    payload: ApproveGenerationRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id, allowed_roles=_REVIEWER_ROLES)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await approve_generation_and_publish(
                session,
                tenant_id=actor.tenant_id,
                request_id=request_id,
                reviewer_id=actor.user_id,
                reason=payload.reason,
            )
    except GenerationNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except GenerationPolicyViolationError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_publish(view)),
        headers={"X-Correlation-ID": correlation_id},
    )
