"""FastAPI /api/v1 routes for C03 Assessment Frameworks, Blueprints & Shortage Validation."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.contracts.blueprints import (
    BlueprintMatrixCell,
    CognitiveLevel,
    FrameworkSpecContract,
    SectionCode,
    SectionSpecContract,
)
from app.contracts.enums import QuestionItemType
from app.modules.frameworks.internal.blueprints import MatrixKey
from app.modules.frameworks.internal.service import (
    THPTQG_2025_VERSION_CODE,
    AssessmentFrameworkView,
    BlueprintNotFoundError,
    BlueprintValidationReportView,
    BlueprintView,
    FrameworkNotFoundError,
    FrameworkValidationError,
    create_assessment_framework,
    create_blueprint,
    validate_blueprint_and_shortage,
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

router = APIRouter(prefix="/api/v1", tags=["frameworks"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]
_AUTHOR_ROLES = frozenset({"TEACHER", "EDITOR", "REVIEWER", "ADMIN", "CONTENT_ADMIN", "SYSTEM_ADMIN"})


class SectionSpecPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_code: SectionCode
    item_type: QuestionItemType
    question_count: int = Field(ge=0)
    points_per_question: Decimal = Field(ge=Decimal("0.00"))


class FrameworkSubjectVersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_code: str = Field(min_length=1, max_length=50)
    duration_seconds: int = Field(gt=0)
    total_questions: int = Field(gt=0)
    total_points: Decimal = Field(default=Decimal("10.00"), gt=Decimal("0.00"))
    sections: list[SectionSpecPayload] = Field(min_length=1)


class CreateAssessmentFrameworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=255)
    version_code: str = Field(default=THPTQG_2025_VERSION_CODE, min_length=1, max_length=40)
    versions: list[FrameworkSubjectVersionPayload] = Field(default_factory=list)


class BlueprintMatrixCellPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_code: str = Field(min_length=1, max_length=120)
    cognitive_level: CognitiveLevel
    item_type: QuestionItemType
    required_count: int = Field(ge=0)


class CreateBlueprintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_code: str = Field(default="THPTQG_2025", min_length=1, max_length=60)
    version_code: str = Field(default=THPTQG_2025_VERSION_CODE, min_length=1, max_length=40)
    subject_code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    duration_seconds: int = Field(gt=0)
    total_points: Decimal = Field(default=Decimal("10.00"), gt=Decimal("0.00"))
    sections: list[SectionSpecPayload] = Field(min_length=1)
    matrix_cells: list[BlueprintMatrixCellPayload] = Field(default_factory=list)


class AvailableCountOverridePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_code: str = Field(min_length=1, max_length=120)
    cognitive_level: CognitiveLevel
    item_type: QuestionItemType
    available_count: int = Field(ge=0)


class ValidateBlueprintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_counts: list[AvailableCountOverridePayload] | None = None


class FrameworkVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    framework_id: uuid.UUID
    version_code: str
    subject_code: str
    total_questions: int
    duration_seconds: int
    total_points: str
    sections: list[SectionSpecPayload]


class AssessmentFrameworkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    code: str
    name: str
    created_at: datetime
    versions: list[FrameworkVersionResponse]


class BlueprintResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    framework_id: uuid.UUID
    framework_code: str
    subject_code: str
    name: str
    duration_seconds: int
    total_points: str
    sections: list[SectionSpecPayload]
    matrix_cells: list[BlueprintMatrixCellPayload]
    created_at: datetime


class BlueprintValidationErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str | None


class ShortageCellResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_code: str
    cognitive_level: CognitiveLevel
    item_type: QuestionItemType
    required_count: int
    available_count: int
    shortage_count: int


class ShortageReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_required: int
    total_available_matched: int
    total_shortage: int
    has_shortage: bool
    shortages: list[ShortageCellResponse]


class BlueprintValidationReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blueprint_id: uuid.UUID
    framework_code: str
    subject_code: str
    valid: bool
    errors: list[BlueprintValidationErrorResponse]
    shortage_report: ShortageReportResponse


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


def _require_author_role(actor: ActorContext, request: Request, correlation_id: str) -> JSONResponse | None:
    if not ({r.upper() for r in actor.roles} & _AUTHOR_ROLES):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Insufficient role permissions for assessment framework operations",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return None


def _serialize_sections(sections: tuple[SectionSpecContract, ...]) -> list[SectionSpecPayload]:
    return [
        SectionSpecPayload(
            section_code=sec.section_code,
            item_type=sec.item_type,
            question_count=sec.question_count,
            points_per_question=sec.points_per_question,
        )
        for sec in sections
    ]


def _serialize_framework(fw: AssessmentFrameworkView) -> AssessmentFrameworkResponse:
    return AssessmentFrameworkResponse(
        id=fw.id,
        code=fw.code,
        name=fw.name,
        created_at=fw.created_at,
        versions=[
            FrameworkVersionResponse(
                id=ver.id,
                framework_id=ver.framework_id,
                version_code=ver.version_code,
                subject_code=ver.subject_code,
                total_questions=ver.total_questions,
                duration_seconds=ver.duration_seconds,
                total_points=str(ver.total_points),
                sections=_serialize_sections(ver.sections),
            )
            for ver in fw.versions
        ],
    )


def _serialize_blueprint(bp: BlueprintView) -> BlueprintResponse:
    return BlueprintResponse(
        id=bp.id,
        tenant_id=bp.tenant_id,
        framework_id=bp.framework_id,
        framework_code=bp.framework_code,
        subject_code=bp.subject_code,
        name=bp.name,
        duration_seconds=bp.duration_seconds,
        total_points=str(bp.total_points),
        sections=_serialize_sections(bp.sections),
        matrix_cells=[
            BlueprintMatrixCellPayload(
                taxonomy_node_code=cell.taxonomy_node_code,
                cognitive_level=cell.cognitive_level,
                item_type=cell.item_type,
                required_count=cell.required_count,
            )
            for cell in bp.matrix_cells
        ],
        created_at=bp.created_at,
    )


def _serialize_validation_report(report: BlueprintValidationReportView) -> BlueprintValidationReportResponse:
    sr = report.shortage_report
    return BlueprintValidationReportResponse(
        blueprint_id=report.blueprint_id,
        framework_code=report.framework_code,
        subject_code=report.subject_code,
        valid=report.valid,
        errors=[
            BlueprintValidationErrorResponse(
                code=err.code,
                message=err.message,
                field=err.field,
            )
            for err in report.errors
        ],
        shortage_report=ShortageReportResponse(
            total_required=sr.total_required,
            total_available_matched=sr.total_available_matched,
            total_shortage=sr.total_shortage,
            has_shortage=sr.has_shortage,
            shortages=[
                ShortageCellResponse(
                    taxonomy_node_code=cell.taxonomy_node_code,
                    cognitive_level=cell.cognitive_level,
                    item_type=cell.item_type,
                    required_count=cell.required_count,
                    available_count=cell.available_count,
                    shortage_count=cell.shortage_count,
                )
                for cell in sr.shortages
            ],
        ),
    )


@router.post(
    "/assessment-frameworks",
    response_model=AssessmentFrameworkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assessment_framework_endpoint(
    payload: CreateAssessmentFrameworkRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    role_err = _require_author_role(auth, request, correlation_id)
    if role_err is not None:
        return role_err

    version_specs = tuple(
        FrameworkSpecContract(
            framework_code=payload.code.strip().upper(),
            subject_code=v.subject_code.strip().upper(),
            duration_seconds=v.duration_seconds,
            total_questions=v.total_questions,
            total_points=v.total_points,
            sections=tuple(
                SectionSpecContract(
                    section_code=sec.section_code,
                    item_type=sec.item_type,
                    question_count=sec.question_count,
                    points_per_question=sec.points_per_question,
                )
                for sec in v.sections
            ),
        )
        for v in payload.versions
    )

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            fw = await create_assessment_framework(
                session,
                code=payload.code,
                name=payload.name,
                version_code=payload.version_code,
                versions=version_specs,
            )
    except FrameworkValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_framework(fw)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post(
    "/blueprints",
    response_model=BlueprintResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_blueprint_endpoint(
    payload: CreateBlueprintRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    role_err = _require_author_role(auth, request, correlation_id)
    if role_err is not None:
        return role_err

    sections = tuple(
        SectionSpecContract(
            section_code=sec.section_code,
            item_type=sec.item_type,
            question_count=sec.question_count,
            points_per_question=sec.points_per_question,
        )
        for sec in payload.sections
    )
    matrix_cells = tuple(
        BlueprintMatrixCell(
            taxonomy_node_code=cell.taxonomy_node_code,
            cognitive_level=cell.cognitive_level,
            item_type=cell.item_type,
            required_count=cell.required_count,
        )
        for cell in payload.matrix_cells
    )

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            bp = await create_blueprint(
                session,
                tenant_id=auth.tenant_id,
                framework_code=payload.framework_code,
                name=payload.name,
                subject_code=payload.subject_code,
                duration_seconds=payload.duration_seconds,
                total_points=payload.total_points,
                sections=sections,
                matrix_cells=matrix_cells,
                version_code=payload.version_code,
            )
    except FrameworkNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except FrameworkValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_blueprint(bp)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post(
    "/blueprints/{blueprint_id}/validate",
    response_model=BlueprintValidationReportResponse,
)
async def validate_blueprint_endpoint(
    blueprint_id: uuid.UUID,
    request: Request,
    payload: ValidateBlueprintRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    role_err = _require_author_role(auth, request, correlation_id)
    if role_err is not None:
        return role_err

    override_map: dict[MatrixKey, int] | None = None
    if payload is not None and payload.available_counts is not None:
        override_map = {
            (entry.taxonomy_node_code, entry.cognitive_level, entry.item_type): entry.available_count
            for entry in payload.available_counts
        }

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            report = await validate_blueprint_and_shortage(
                session,
                tenant_id=auth.tenant_id,
                blueprint_id=blueprint_id,
                available_counts=override_map,
            )
    except BlueprintNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except FrameworkValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_validation_report(report)),
        headers={"X-Correlation-ID": correlation_id},
    )
