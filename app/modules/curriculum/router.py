"""FastAPI /api/v1 routes for B06 curriculum versions, taxonomy mappings, and CoverageReport."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.modules.curriculum.internal.service import (
    CoverageReportRecord,
    CurriculumNodeInputSpec,
    CurriculumNodeRecord,
    CurriculumVersionRecord,
    DuplicateMappingError,
    TaxonomyMappingRecord,
    create_curriculum_with_nodes,
    fork_curriculum_version,
    get_curriculum_coverage,
    link_curriculum_to_taxonomy,
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
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["curriculum"])

_EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class CurriculumNodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_level: Literal["CHAPTER", "LESSON", "SECTION", "PROBLEM_TYPE"]
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=255)
    parent_code: str | None = Field(default=None, min_length=1, max_length=120)
    sequence_order: int = Field(default=0, ge=0)
    children: list[CurriculumNodeInput] = Field(default_factory=list)


CurriculumNodeInput.model_rebuild()


class CurriculumVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    code: str
    name: str
    subject_code: str
    grade_band: str
    version_label: str
    status: str
    created_at: datetime


class CurriculumNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    parent_id: uuid.UUID | None
    node_level: str
    code: str
    title: str
    sequence_order: int


class CreateCurriculumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    subject_code: str = Field(min_length=1, max_length=50)
    grade_band: str = Field(min_length=1, max_length=30)
    version_label: str = Field(default="v1", min_length=1, max_length=40)
    visibility_scope: Literal["TENANT_PRIVATE", "SYSTEM_PUBLIC"] = "TENANT_PRIVATE"
    status: Literal["DRAFT", "PUBLISHED", "ARCHIVED"] = "PUBLISHED"
    nodes: list[CurriculumNodeInput] = Field(default_factory=list)


class CreateCurriculumVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_label: str = Field(min_length=1, max_length=40)
    code: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    visibility_scope: Literal["TENANT_PRIVATE", "SYSTEM_PUBLIC"] = "TENANT_PRIVATE"
    status: Literal["DRAFT", "PUBLISHED", "ARCHIVED"] = "DRAFT"
    copy_nodes: bool = True
    copy_mappings: bool = True
    nodes: list[CurriculumNodeInput] = Field(default_factory=list)


class CurriculumDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curriculum: CurriculumVersionResponse
    nodes: list[CurriculumNodeResponse]


class CreateCurriculumMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curriculum_node_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    mapping_weight: Decimal = Field(default=Decimal("1.00"), gt=Decimal("0"), le=Decimal("99.99"))


class TaxonomyMappingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    curriculum_node_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    mapping_weight: Decimal


class CognitiveCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    NB: int
    TH: int
    VD: int
    VDC: int
    total: int


class CoverageNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curriculum_node_id: uuid.UUID
    parent_id: uuid.UUID | None
    node_level: str
    code: str
    title: str
    mapped_taxonomy_node_ids: list[uuid.UUID]
    mapped_taxonomy_count: int
    is_mapped: bool
    question_counts: CognitiveCountsResponse


class CoverageReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curriculum_version_id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    name: str
    subject_code: str
    grade_band: str
    version_label: str
    total_nodes: int
    reported_node_count: int
    total_problem_types: int
    mapped_node_count: int
    unmapped_node_count: int
    mapping_coverage_ratio: float
    question_counts: CognitiveCountsResponse
    question_bank_available: bool
    generated_in_ms: float
    items: list[CoverageNodeResponse]


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
                detail="editor or admin role required to modify curriculum data",
                request=request,
                correlation_id=correlation_id,
            )

    return actor, correlation_id, None


def _to_node_specs(items: list[CurriculumNodeInput]) -> tuple[CurriculumNodeInputSpec, ...]:
    return tuple(
        CurriculumNodeInputSpec(
            node_level=item.node_level,
            code=item.code,
            title=item.title,
            parent_code=item.parent_code,
            sequence_order=item.sequence_order,
            children=_to_node_specs(item.children),
        )
        for item in items
    )


def _serialize_version(ver: CurriculumVersionRecord) -> CurriculumVersionResponse:
    return CurriculumVersionResponse(
        id=ver.id,
        tenant_id=ver.tenant_id,
        visibility_scope=ver.visibility_scope,
        code=ver.code,
        name=ver.name,
        subject_code=ver.subject_code,
        grade_band=ver.grade_band,
        version_label=ver.version_label,
        status=ver.status,
        created_at=ver.created_at,
    )


def _serialize_node(node: CurriculumNodeRecord) -> CurriculumNodeResponse:
    return CurriculumNodeResponse(
        id=node.id,
        tenant_id=node.tenant_id,
        curriculum_version_id=node.curriculum_version_id,
        parent_id=node.parent_id,
        node_level=node.node_level,
        code=node.code,
        title=node.title,
        sequence_order=node.sequence_order,
    )


def _serialize_mapping(rec: TaxonomyMappingRecord) -> TaxonomyMappingResponse:
    return TaxonomyMappingResponse(
        id=rec.id,
        tenant_id=rec.tenant_id,
        curriculum_node_id=rec.curriculum_node_id,
        taxonomy_node_id=rec.taxonomy_node_id,
        mapping_weight=rec.mapping_weight,
    )


def _serialize_coverage(report: CoverageReportRecord) -> CoverageReportResponse:
    return CoverageReportResponse(
        curriculum_version_id=report.curriculum_version_id,
        tenant_id=report.tenant_id,
        code=report.code,
        name=report.name,
        subject_code=report.subject_code,
        grade_band=report.grade_band,
        version_label=report.version_label,
        total_nodes=report.total_nodes,
        reported_node_count=report.reported_node_count,
        total_problem_types=report.total_problem_types,
        mapped_node_count=report.mapped_node_count,
        unmapped_node_count=report.unmapped_node_count,
        mapping_coverage_ratio=report.mapping_coverage_ratio,
        question_counts=CognitiveCountsResponse(
            NB=report.question_counts.NB,
            TH=report.question_counts.TH,
            VD=report.question_counts.VD,
            VDC=report.question_counts.VDC,
            total=report.question_counts.total,
        ),
        question_bank_available=report.question_bank_available,
        generated_in_ms=report.generated_in_ms,
        items=[
            CoverageNodeResponse(
                curriculum_node_id=item.curriculum_node_id,
                parent_id=item.parent_id,
                node_level=item.node_level,
                code=item.code,
                title=item.title,
                mapped_taxonomy_node_ids=list(item.mapped_taxonomy_node_ids),
                mapped_taxonomy_count=item.mapped_taxonomy_count,
                is_mapped=item.is_mapped,
                question_counts=CognitiveCountsResponse(
                    NB=item.question_counts.NB,
                    TH=item.question_counts.TH,
                    VD=item.question_counts.VD,
                    VDC=item.question_counts.VDC,
                    total=item.question_counts.total,
                ),
            )
            for item in report.items
        ],
    )


@router.post("/curricula", response_model=CurriculumDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_curriculum(
    body: CreateCurriculumRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> CurriculumDetailResponse | JSONResponse:
    """Create a curriculum edition with its optional 4-level node hierarchy."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=True)
    if err is not None:
        return err
    assert actor is not None

    if body.visibility_scope == "SYSTEM_PUBLIC" and actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="only SYSTEM_PUBLIC actors may publish SYSTEM_PUBLIC curricula",
            request=request,
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            ver, nodes = await create_curriculum_with_nodes(
                session,
                tenant_id=actor.tenant_id,
                code=body.code,
                name=body.name,
                subject_code=body.subject_code,
                grade_band=body.grade_band,
                version_label=body.version_label,
                visibility_scope=body.visibility_scope,
                status=body.status,
                nodes=_to_node_specs(body.nodes),
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
            detail="curriculum code conflicts with an existing curriculum version",
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_409_CONFLICT,
        )

    return CurriculumDetailResponse(
        curriculum=_serialize_version(ver),
        nodes=[_serialize_node(n) for n in nodes],
    )


@router.post("/curricula/{id}/versions", response_model=CurriculumDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_curriculum_new_version(
    id: uuid.UUID,
    body: CreateCurriculumVersionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> CurriculumDetailResponse | JSONResponse:
    """Create a new version based on an existing curriculum edition (cloning nodes/mappings by default)."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=True)
    if err is not None:
        return err
    assert actor is not None

    if body.visibility_scope == "SYSTEM_PUBLIC" and actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="only SYSTEM_PUBLIC actors may publish SYSTEM_PUBLIC curriculum versions",
            request=request,
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            ver, nodes = await fork_curriculum_version(
                session,
                source_curriculum_id=id,
                tenant_id=actor.tenant_id,
                version_label=body.version_label,
                code=body.code,
                name=body.name,
                visibility_scope=body.visibility_scope,
                status=body.status,
                copy_nodes=body.copy_nodes,
                copy_mappings=body.copy_mappings,
                nodes=_to_node_specs(body.nodes),
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
            detail="curriculum version code conflicts with an existing curriculum version",
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_409_CONFLICT,
        )

    return CurriculumDetailResponse(
        curriculum=_serialize_version(ver),
        nodes=[_serialize_node(n) for n in nodes],
    )


@router.post("/curriculum-mappings", response_model=TaxonomyMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_curriculum_mapping(
    body: CreateCurriculumMappingRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> TaxonomyMappingResponse | JSONResponse:
    """Create a many-to-many link between a curriculum node and a visible taxonomy node."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=True)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            mapping = await link_curriculum_to_taxonomy(
                session,
                tenant_id=actor.tenant_id,
                curriculum_node_id=body.curriculum_node_id,
                taxonomy_node_id=body.taxonomy_node_id,
                mapping_weight=body.mapping_weight,
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
    except DuplicateMappingError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_409_CONFLICT,
        )

    return _serialize_mapping(mapping)


@router.get("/curricula/{id}/coverage", response_model=CoverageReportResponse)
async def get_coverage_report(
    id: uuid.UUID,
    request: Request,
    node_level: str | None = Query(default=None),
    published_only: bool = Query(default=True),
    credentials: OptionalBearer = None,
) -> CoverageReportResponse | JSONResponse:
    """Return B06 CoverageReport across curriculum problem types/nodes."""
    actor, correlation_id, err = _authenticate_actor(request, credentials, require_editor=False)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            report = await get_curriculum_coverage(
                session,
                curriculum_version_id=id,
                node_level=node_level,
                published_only=published_only,
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

    return _serialize_coverage(report)
