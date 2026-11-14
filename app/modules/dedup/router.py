"""FastAPI /api/v1 routes for A08 3-tier question deduplication & cluster decisions."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.dedup.internal.service import (
    CandidateNotFoundError,
    DedupMatchResult,
    DedupValidationError,
    DuplicateClusterNotFoundError,
    DuplicateClusterView,
    decide_duplicate_cluster,
    get_duplicate_cluster,
    match_question_candidate,
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

router = APIRouter(prefix="/api/v1", tags=["dedup"])

_DEDUP_ROLES = frozenset(
    {"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "REVIEWER", "TEACHER", "CONTENT_AUTHOR"}
)
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class DedupMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: uuid.UUID | None = None
    stem_md: str = Field(min_length=1)
    options_md: list[str] = Field(default_factory=list)
    semantic_threshold: Decimal = Field(default=Decimal("0.550"), gt=Decimal("0"), le=Decimal("1"))
    enable_semantic_tier: bool = True


class DedupMatchCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: uuid.UUID
    match_tier: str
    similarity_score: float
    existing_cluster_id: uuid.UUID | None
    stem_md: str


class DuplicateMemberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: uuid.UUID
    candidate_id: uuid.UUID
    tenant_id: uuid.UUID
    similarity_score: float


class DuplicateClusterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    cluster_type: str
    canonical_question_id: uuid.UUID | None
    status: str
    created_at: datetime
    members: list[DuplicateMemberResponse]


class DedupMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_hash: str
    structural_hash: str
    cluster: DuplicateClusterResponse | None
    matches: list[DedupMatchCandidateResponse]


class DecideDuplicateClusterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["RESOLVED_MERGED", "RESOLVED_KEEP_VARIANTS"]
    canonical_question_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        status_override=status_code,
        instance=instance,
        correlation_id=correlation_id,
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


def credentials_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    return credentials.credentials if credentials else None


def _require_role(actor: ActorContext, request: Request, correlation_id: str) -> JSONResponse | None:
    if not ({r.upper() for r in actor.roles} & _DEDUP_ROLES):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Insufficient role permissions for dedup operations",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return None


def _cluster_to_response(view: DuplicateClusterView) -> DuplicateClusterResponse:
    return DuplicateClusterResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        cluster_type=view.cluster_type,
        canonical_question_id=view.canonical_question_id,
        status=view.status,
        created_at=view.created_at,
        members=[
            DuplicateMemberResponse(
                cluster_id=m.cluster_id,
                candidate_id=m.candidate_id,
                tenant_id=m.tenant_id,
                similarity_score=float(m.similarity_score),
            )
            for m in view.members
        ],
    )


def _match_to_response(result: DedupMatchResult) -> DedupMatchResponse:
    return DedupMatchResponse(
        content_hash=result.content_hash,
        structural_hash=result.structural_hash,
        cluster=_cluster_to_response(result.cluster) if result.cluster else None,
        matches=[
            DedupMatchCandidateResponse(
                candidate_id=m.candidate_id,
                match_tier=m.match_tier,
                similarity_score=float(m.similarity_score),
                existing_cluster_id=m.existing_cluster_id,
                stem_md=m.stem_md,
            )
            for m in result.matches
        ],
    )


@router.post("/dedup/match", status_code=status.HTTP_200_OK, response_model=DedupMatchResponse)
async def match_question_duplicates_endpoint(
    payload: DedupMatchRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> DedupMatchResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            result = await match_question_candidate(
                session,
                tenant_id=auth.tenant_id,
                candidate_id=payload.candidate_id,
                stem_md=payload.stem_md,
                options_md=payload.options_md,
                semantic_threshold=payload.semantic_threshold,
                enable_semantic_tier=payload.enable_semantic_tier,
                correlation_id=correlation_id,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(_match_to_response(result)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except CandidateNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except DedupValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )


@router.get("/duplicate-clusters/{cluster_id}", status_code=status.HTTP_200_OK, response_model=DuplicateClusterResponse)
async def get_duplicate_cluster_endpoint(
    cluster_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> DuplicateClusterResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            view = await get_duplicate_cluster(session, cluster_id=cluster_id)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(_cluster_to_response(view)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except DuplicateClusterNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )


@router.post(
    "/duplicate-clusters/{cluster_id}/decisions",
    status_code=status.HTTP_200_OK,
    response_model=DuplicateClusterResponse,
)
async def decide_duplicate_cluster_endpoint(
    cluster_id: uuid.UUID,
    payload: DecideDuplicateClusterRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> DuplicateClusterResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            view = await decide_duplicate_cluster(
                session,
                tenant_id=auth.tenant_id,
                cluster_id=cluster_id,
                decision=payload.decision,
                canonical_question_id=payload.canonical_question_id,
                decided_by=auth.user_id,
                note=payload.note,
                correlation_id=correlation_id,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(_cluster_to_response(view)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except DuplicateClusterNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except DedupValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
