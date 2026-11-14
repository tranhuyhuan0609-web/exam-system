"""FastAPI /api/v1 routes for B04 taxonomy tree queries, node creation, and versioning."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.modules.taxonomy.internal.service import (
    TaxonomyNodeRecord,
    TaxonomyNodeVersionRecord,
    TaxonomyNodeViewRecord,
    add_taxonomy_alias,
    create_node_version,
    create_taxonomy_node,
    list_taxonomy_nodes,
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
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.pagination import CursorError, resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1/taxonomy", tags=["taxonomy"])

_EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class TaxonomyNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    parent_id: uuid.UUID | None
    node_type: str
    code: str
    code_path: str
    current_version_no: int
    is_active: bool
    created_at: datetime
    name: str | None = None
    description_md: str | None = None


class TaxonomyNodeVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    version_no: int
    name: str
    description_md: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class TaxonomyNodeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TaxonomyNodeResponse]
    next_cursor: str | None
    has_more: bool
    limit: int


class CreateTaxonomyNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: Literal["DOMAIN", "SUBJECT", "TOPIC", "CONCEPT", "SKILL"]
    code: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    description_md: str | None = None
    parent_id: uuid.UUID | None = None
    code_path: str | None = Field(default=None, min_length=1, max_length=512)
    visibility_scope: Literal["TENANT_PRIVATE", "SYSTEM_PUBLIC", "SHARED_PARTNER"] = "TENANT_PRIVATE"
    is_active: bool = True
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CreateTaxonomyNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: TaxonomyNodeResponse
    version: TaxonomyNodeVersionResponse
    alias_ids: list[uuid.UUID]


class CreateTaxonomyVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description_md: str | None = None


class CreateTaxonomyVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: TaxonomyNodeResponse
    version: TaxonomyNodeVersionResponse


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


def _authenticate_actor(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    *,
    require_editor: bool = False,
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

    if require_editor:
        normalized_roles = {role.upper() for role in actor.roles}
        if not (normalized_roles & _EDITOR_ROLES):
            return None, correlation_id, _problem_response(
                code=PlatformErrorCode.FORBIDDEN,
                detail="editor or admin role required to modify taxonomy",
                request=request,
                correlation_id=correlation_id,
            )

    return actor, correlation_id, None


def _serialize_node(
    node: TaxonomyNodeRecord | TaxonomyNodeViewRecord,
    *,
    name: str | None = None,
    description_md: str | None = None,
) -> TaxonomyNodeResponse:
    resolved_name = node.name if isinstance(node, TaxonomyNodeViewRecord) else name
    resolved_desc = node.description_md if isinstance(node, TaxonomyNodeViewRecord) else description_md
    return TaxonomyNodeResponse(
        id=node.id,
        tenant_id=node.tenant_id,
        visibility_scope=node.visibility_scope,
        parent_id=node.parent_id,
        node_type=node.node_type,
        code=node.code,
        code_path=node.code_path,
        current_version_no=node.current_version_no,
        is_active=node.is_active,
        created_at=node.created_at,
        name=resolved_name,
        description_md=resolved_desc,
    )


def _serialize_version(ver: TaxonomyNodeVersionRecord) -> TaxonomyNodeVersionResponse:
    return TaxonomyNodeVersionResponse(
        id=ver.id,
        tenant_id=ver.tenant_id,
        taxonomy_node_id=ver.taxonomy_node_id,
        version_no=ver.version_no,
        name=ver.name,
        description_md=ver.description_md,
        created_by=ver.created_by,
        created_at=ver.created_at,
    )


@router.get("/nodes", response_model=TaxonomyNodeListResponse)
async def list_nodes(
    request: Request,
    root_path: str | None = Query(default=None),
    include_root: bool = Query(default=True),
    node_type: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    credentials: OptionalBearer = None,
) -> TaxonomyNodeListResponse | JSONResponse:
    """List visible taxonomy nodes, optionally filtered by ltree `<@` subtree."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=False)
    if err is not None:
        return err
    assert actor is not None

    settings = get_settings()
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            page = await list_taxonomy_nodes(
                session,
                tenant_id=actor.tenant_id,
                cursor_secret=settings.secret_key,
                root_path=root_path,
                include_root=include_root,
                node_type=node_type,
                active_only=active_only,
                limit=limit,
                cursor=cursor,
            )
    except (CursorError, ValueError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return TaxonomyNodeListResponse(
        items=[_serialize_node(item) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        limit=page.limit,
    )


@router.post("/nodes", response_model=CreateTaxonomyNodeResponse, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: CreateTaxonomyNodeRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> CreateTaxonomyNodeResponse | JSONResponse:
    """Create a taxonomy node + initial version v1 under the authenticated tenant."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=True)
    if err is not None:
        return err
    assert actor is not None

    if body.visibility_scope == "SYSTEM_PUBLIC" and actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="only SYSTEM_PUBLIC actors may publish SYSTEM_PUBLIC taxonomy nodes",
            request=request,
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            node, version = await create_taxonomy_node(
                session,
                tenant_id=actor.tenant_id,
                node_type=body.node_type,
                code=body.code,
                name=body.name,
                description_md=body.description_md,
                parent_id=body.parent_id,
                code_path=body.code_path,
                visibility_scope=body.visibility_scope,
                created_by=actor.user_id,
                is_active=body.is_active,
            )
            alias_ids: list[uuid.UUID] = []
            for raw_alias in body.aliases:
                alias_ids.append(
                    await add_taxonomy_alias(
                        session,
                        tenant_id=actor.tenant_id,
                        taxonomy_node_id=node.id,
                        alias_name=raw_alias,
                    )
                )
    except LookupError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except IntegrityError:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail="taxonomy node code, code_path, or alias conflicts with an existing record",
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_409_CONFLICT,
        )

    return CreateTaxonomyNodeResponse(
        node=_serialize_node(node, name=version.name, description_md=version.description_md),
        version=_serialize_version(version),
        alias_ids=alias_ids,
    )


@router.post("/nodes/{id}/versions", response_model=CreateTaxonomyVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_version(
    id: uuid.UUID,
    body: CreateTaxonomyVersionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> CreateTaxonomyVersionResponse | JSONResponse:
    """Append version N+1 to an existing taxonomy node without mutating id or code_path."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=True)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            node, version = await create_node_version(
                session,
                taxonomy_node_id=id,
                name=body.name,
                description_md=body.description_md,
                created_by=actor.user_id,
                actor_tenant_id=actor.tenant_id,
            )
    except LookupError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except PermissionError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return CreateTaxonomyVersionResponse(
        node=_serialize_node(node, name=version.name, description_md=version.description_md),
        version=_serialize_version(version),
    )
