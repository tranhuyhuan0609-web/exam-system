"""FastAPI /api/v1 routes for B07 Search, Discovery & Collections."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.search.internal.service import (
    CollectionItemView,
    CollectionNotFoundError,
    CollectionView,
    SearchHitView,
    SearchValidationError,
    add_collection_item,
    create_collection,
    get_collection,
    search_documents_fts,
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

router = APIRouter(prefix="/api/v1", tags=["search"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class CreateCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)


class AddCollectionItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: str = Field(min_length=1, max_length=30)
    item_id: uuid.UUID
    sequence_order: int | None = Field(default=None, ge=0)


class SearchHitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    doc_type: str
    entity_id: uuid.UUID
    title: str
    snippet: str
    subject_code: str | None
    grade_band: str | None
    taxonomy_path: str | None
    score: float
    updated_at: datetime


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SearchHitResponse]
    total_count: int
    limit: int
    offset: int
    was_truncated: bool
    normalized_query: str


class CollectionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_id: uuid.UUID
    tenant_id: uuid.UUID
    item_type: str
    item_id: uuid.UUID
    sequence_order: int


class CollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    owner_membership_id: uuid.UUID
    title: str
    created_at: datetime
    items: list[CollectionItemResponse]


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


def _serialize_hit(hit: SearchHitView) -> SearchHitResponse:
    return SearchHitResponse(
        id=hit.id,
        tenant_id=hit.tenant_id,
        visibility_scope=hit.visibility_scope,
        doc_type=hit.doc_type,
        entity_id=hit.entity_id,
        title=hit.title,
        snippet=hit.snippet,
        subject_code=hit.subject_code,
        grade_band=hit.grade_band,
        taxonomy_path=hit.taxonomy_path,
        score=hit.score,
        updated_at=hit.updated_at,
    )


def _serialize_item(item: CollectionItemView) -> CollectionItemResponse:
    return CollectionItemResponse(
        collection_id=item.collection_id,
        tenant_id=item.tenant_id,
        item_type=item.item_type,
        item_id=item.item_id,
        sequence_order=item.sequence_order,
    )


def _serialize_collection(col: CollectionView) -> CollectionResponse:
    return CollectionResponse(
        id=col.id,
        tenant_id=col.tenant_id,
        owner_membership_id=col.owner_membership_id,
        title=col.title,
        created_at=col.created_at,
        items=[_serialize_item(it) for it in col.items],
    )


@router.get("/search", response_model=SearchResponse)
async def search_documents_endpoint(
    request: Request,
    credentials: OptionalBearer = None,
    q: str = Query(default=""),
    doc_type: str | None = Query(default=None),
    subject_code: str | None = Query(default=None),
    grade_band: str | None = Query(default=None),
    taxonomy_path: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            result = await search_documents_fts(
                session,
                query_text=q,
                doc_type=doc_type,
                subject_code=subject_code,
                grade_band=grade_band,
                taxonomy_path=taxonomy_path,
                limit=limit,
                offset=offset,
            )
    except SearchValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    body = SearchResponse(
        items=[_serialize_hit(item) for item in result.items],
        total_count=result.total_count,
        limit=result.limit,
        offset=result.offset,
        was_truncated=result.was_truncated,
        normalized_query=result.normalized_query,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/collections", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection_endpoint(
    payload: CreateCollectionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            collection = await create_collection(
                session,
                tenant_id=auth.tenant_id,
                owner_membership_id=auth.membership_id,
                title=payload.title,
            )
    except SearchValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_collection(collection)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/collections/{collection_id}", response_model=CollectionResponse)
async def get_collection_endpoint(
    collection_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            collection = await get_collection(
                session,
                tenant_id=auth.tenant_id,
                collection_id=collection_id,
            )
    except CollectionNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_collection(collection)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post(
    "/collections/{collection_id}/items",
    response_model=CollectionItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_collection_item_endpoint(
    collection_id: uuid.UUID,
    payload: AddCollectionItemRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            item = await add_collection_item(
                session,
                tenant_id=auth.tenant_id,
                collection_id=collection_id,
                item_type=payload.item_type,
                item_id=payload.item_id,
                sequence_order=payload.sequence_order,
            )
    except CollectionNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except SearchValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_item(item)),
        headers={"X-Correlation-ID": correlation_id},
    )
