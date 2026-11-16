"""FastAPI /api/v1 routes for B05 Knowledge Graph."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.knowledge_graph.internal.service import (
    GraphValidationRunView,
    KnowledgeEdgeView,
    KnowledgeGraphNotFoundError,
    KnowledgeGraphValidationError,
    PrerequisiteCycleError,
    create_knowledge_edge,
    get_prerequisite_path,
    list_knowledge_edges,
    validate_knowledge_graph,
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

router = APIRouter(prefix="/api/v1/knowledge-graph", tags=["knowledge-graph"])

_EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "CONTENT_AUTHOR"})


class CreateKnowledgeEdgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    edge_type: str = Field(default="PREREQUISITE", min_length=1, max_length=40)
    confidence: Decimal = Field(default=Decimal("1.000"), ge=Decimal("0"), le=Decimal("1"))
    graph_version: int = Field(default=1, ge=1)


class ValidateGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_version: int = Field(default=1, ge=1)


class KnowledgeEdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    edge_type: str
    confidence: Decimal
    graph_version: int
    created_at: datetime


class GraphValidationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    graph_version: int
    is_dag_valid: bool
    cycle_path: list[uuid.UUID]
    validated_at: datetime


class PrerequisitePathResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_node_id: uuid.UUID
    graph_version: int
    prerequisite_node_ids: list[uuid.UUID]
    edges: list[KnowledgeEdgeResponse]


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
    *,
    require_editor: bool = False,
) -> ActorContext | JSONResponse:
    get_settings()
    try:
        actor = verify_actor(request, credentials, allow_cookie=True)
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

    if require_editor and not ({r.upper() for r in actor.roles} & _EDITOR_ROLES):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="editor or teacher role required to modify knowledge graph",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_edge(edge: KnowledgeEdgeView) -> KnowledgeEdgeResponse:
    return KnowledgeEdgeResponse(
        id=edge.id,
        tenant_id=edge.tenant_id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        edge_type=edge.edge_type,
        confidence=edge.confidence,
        graph_version=edge.graph_version,
        created_at=edge.created_at,
    )


def _serialize_validation(run: GraphValidationRunView) -> GraphValidationRunResponse:
    return GraphValidationRunResponse(
        id=run.id,
        tenant_id=run.tenant_id,
        graph_version=run.graph_version,
        is_dag_valid=run.is_dag_valid,
        cycle_path=list(run.cycle_path),
        validated_at=run.validated_at,
    )


@router.post("/edges", response_model=KnowledgeEdgeResponse, status_code=status.HTTP_201_CREATED)
async def create_edge_endpoint(
    payload: CreateKnowledgeEdgeRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, require_editor=True)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            edge = await create_knowledge_edge(
                session,
                tenant_id=auth.tenant_id,
                source_node_id=payload.source_node_id,
                target_node_id=payload.target_node_id,
                edge_type=payload.edge_type,
                confidence=payload.confidence,
                graph_version=payload.graph_version,
            )
    except PrerequisiteCycleError as exc:
        return _problem_response(
            code=PlatformErrorCode.PREREQUISITE_CYCLE_DETECTED,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"cycle_path": [str(n) for n in exc.cycle_path]},
        )
    except KnowledgeGraphNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except KnowledgeGraphValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_edge(edge)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/edges", response_model=list[KnowledgeEdgeResponse])
async def list_edges_endpoint(
    request: Request,
    graph_version: Annotated[int, Query(ge=1)] = 1,
    edge_type: Annotated[str | None, Query()] = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, require_editor=False)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            edges = await list_knowledge_edges(
                session,
                tenant_id=auth.tenant_id,
                graph_version=graph_version,
                edge_type=edge_type,
            )
    except KnowledgeGraphValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder([_serialize_edge(edge) for edge in edges]),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/path", response_model=PrerequisitePathResponse)
async def get_prerequisite_path_endpoint(
    target_node_id: Annotated[uuid.UUID, Query()],
    request: Request,
    graph_version: Annotated[int, Query(ge=1)] = 1,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, require_editor=False)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            path_view = await get_prerequisite_path(
                session,
                tenant_id=auth.tenant_id,
                target_node_id=target_node_id,
                graph_version=graph_version,
            )
    except KnowledgeGraphValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(
            PrerequisitePathResponse(
                target_node_id=path_view.target_node_id,
                graph_version=path_view.graph_version,
                prerequisite_node_ids=list(path_view.prerequisite_node_ids),
                edges=[_serialize_edge(edge) for edge in path_view.edges],
            )
        ),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/validate", response_model=GraphValidationRunResponse, status_code=status.HTTP_201_CREATED)
async def validate_graph_endpoint(
    request: Request,
    payload: ValidateGraphRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, require_editor=True)
    if isinstance(auth, JSONResponse):
        return auth

    version = payload.graph_version if payload is not None else 1
    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            run = await validate_knowledge_graph(
                session,
                tenant_id=auth.tenant_id,
                graph_version=version,
            )
    except KnowledgeGraphValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_validation(run)),
        headers={"X-Correlation-ID": correlation_id},
    )
