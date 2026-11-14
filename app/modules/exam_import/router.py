"""FastAPI /api/v1 routes for A04 complete exam import, structure review, and approval."""

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
from app.modules.exam_import.internal.service import (
    BrandedPdfRightsDeniedError,
    ExamImportInvalidStateError,
    ExamImportNotFoundError,
    ExamImportStructureView,
    ExamImportValidationError,
    ProvenanceRecordView,
    QuestionDraftInput,
    SectionDraftInput,
    StimulusInput,
    approve_exam_import,
    create_exam_import_session,
    get_exam_import_structure,
    update_exam_import_structure,
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

router = APIRouter(prefix="/api/v1/exam-imports", tags=["exam_import"])

_IMPORT_ROLES = frozenset(
    {"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "REVIEWER", "TEACHER", "CONTENT_AUTHOR"}
)
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class QuestionDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_no: int = Field(ge=1)
    points: float
    page_number: int = Field(default=1, ge=1)
    candidate_id: uuid.UUID | None = None
    stimulus_id: str | None = Field(default=None, max_length=120)
    paired_answer: str | None = None
    item_type: str = Field(default="SINGLE_CHOICE", max_length=40)
    stem_md: str | None = None
    options_md: list[str] = Field(default_factory=list)
    bbox_json: dict[str, Any] | None = None
    original_question_label: str | None = Field(default=None, max_length=60)


class SectionDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_code: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=255)
    questions: list[QuestionDraftPayload] = Field(default_factory=list)


class StimulusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stimulus_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=255)
    page_number: int = Field(default=1, ge=1)


class CreateExamImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_run_id: uuid.UUID
    sections: list[SectionDraftPayload]
    stimuli: list[StimulusPayload] = Field(default_factory=list)
    expected_total_points: float | None = 10.0
    rights_status_override: str | None = None
    first_item_y0_pt: float | None = Field(default=None, ge=0.0)
    create_question_candidates: bool = True


class UpdateExamImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[SectionDraftPayload] | None = None
    stimuli: list[StimulusPayload] | None = None
    expected_total_points: float | None = None


class ApproveExamImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_answer_keys: bool = True
    generate_branded_pdf: bool = False
    banner_text: str = Field(default="H2Edu - Hoc thong minh - Thi but pha", max_length=200)


class BannerPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_page_height_pt: float
    expanded_page_height_pt: float
    top_expansion_pt: float
    shifted_first_item_y0_pt: float
    header_expanded: bool


class ValidationReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    question_count: int
    total_points: float
    branded_pdf_allowed: bool
    errors: list[str]


class ProvenanceRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    extraction_run_id: uuid.UUID
    page_no: int
    bbox_json: dict[str, Any] | list[Any] | None
    section_label: str | None
    original_question_label: str | None
    raw_snippet_text: str | None
    created_at: datetime


class ExamImportStructureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    extraction_run_id: uuid.UUID
    document_version_id: uuid.UUID
    source_document_id: uuid.UUID
    rights_status: str
    status: str
    proposed_structure_json: dict[str, Any]
    validation: ValidationReportResponse
    banner_plan: BannerPlanResponse
    branded_pdf_asset_id: uuid.UUID | None
    provenance_records: list[ProvenanceRecordResponse]
    created_at: datetime


def _credentials_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    return credentials.credentials if credentials else None


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


def _require_role(actor: ActorContext, request: Request, correlation_id: str) -> JSONResponse | None:
    if not ({r.upper() for r in actor.roles} & _IMPORT_ROLES):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Insufficient role permissions for exam import",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return None


def _to_sections(payloads: list[SectionDraftPayload]) -> tuple[SectionDraftInput, ...]:
    return tuple(
        SectionDraftInput(
            section_code=sec.section_code,
            title=sec.title,
            questions=tuple(
                QuestionDraftInput(
                    question_no=q.question_no,
                    points=q.points,
                    page_number=q.page_number,
                    candidate_id=q.candidate_id,
                    stimulus_id=q.stimulus_id,
                    paired_answer=q.paired_answer,
                    item_type=q.item_type,
                    stem_md=q.stem_md,
                    options_md=tuple(q.options_md),
                    bbox_json=q.bbox_json,
                    original_question_label=q.original_question_label,
                )
                for q in sec.questions
            ),
        )
        for sec in payloads
    )


def _to_stimuli(payloads: list[StimulusPayload]) -> tuple[StimulusInput, ...]:
    return tuple(
        StimulusInput(stimulus_id=s.stimulus_id, title=s.title, page_number=s.page_number)
        for s in payloads
    )


def _prov_response(view: ProvenanceRecordView) -> ProvenanceRecordResponse:
    return ProvenanceRecordResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        document_version_id=view.document_version_id,
        extraction_run_id=view.extraction_run_id,
        page_no=view.page_no,
        bbox_json=view.bbox_json,
        section_label=view.section_label,
        original_question_label=view.original_question_label,
        raw_snippet_text=view.raw_snippet_text,
        created_at=view.created_at,
    )


def _structure_to_response(view: ExamImportStructureView) -> ExamImportStructureResponse:
    return ExamImportStructureResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        extraction_run_id=view.extraction_run_id,
        document_version_id=view.document_version_id,
        source_document_id=view.source_document_id,
        rights_status=view.rights_status,
        status=view.status,
        proposed_structure_json=view.proposed_structure_json,
        validation=ValidationReportResponse(
            valid=view.validation.valid,
            question_count=view.validation.question_count,
            total_points=view.validation.total_points,
            branded_pdf_allowed=view.validation.branded_pdf_allowed,
            errors=list(view.validation.errors),
        ),
        banner_plan=BannerPlanResponse(
            original_page_height_pt=view.banner_plan.original_page_height_pt,
            expanded_page_height_pt=view.banner_plan.expanded_page_height_pt,
            top_expansion_pt=view.banner_plan.top_expansion_pt,
            shifted_first_item_y0_pt=view.banner_plan.shifted_first_item_y0_pt,
            header_expanded=view.banner_plan.header_expanded,
        ),
        branded_pdf_asset_id=view.branded_pdf_asset_id,
        provenance_records=[_prov_response(p) for p in view.provenance_records],
        created_at=view.created_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ExamImportStructureResponse)
async def create_exam_import_endpoint(
    payload: CreateExamImportRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> ExamImportStructureResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            view = await create_exam_import_session(
                session,
                tenant_id=auth.tenant_id,
                extraction_run_id=payload.extraction_run_id,
                sections=_to_sections(payload.sections),
                stimuli=_to_stimuli(payload.stimuli),
                expected_total_points=payload.expected_total_points,
                rights_status_override=payload.rights_status_override,
                first_item_y0_pt=payload.first_item_y0_pt,
                create_question_candidates=payload.create_question_candidates,
                correlation_id=correlation_id,
            )
            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content=jsonable_encoder(_structure_to_response(view)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except ExamImportNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ExamImportValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )


@router.get("/{import_id}/structure", status_code=status.HTTP_200_OK, response_model=ExamImportStructureResponse)
async def get_exam_import_structure_endpoint(
    import_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> ExamImportStructureResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            view = await get_exam_import_structure(session, import_id=import_id)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(_structure_to_response(view)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except ExamImportNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )


@router.patch("/{import_id}/structure", status_code=status.HTTP_200_OK, response_model=ExamImportStructureResponse)
async def update_exam_import_structure_endpoint(
    import_id: uuid.UUID,
    payload: UpdateExamImportRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> ExamImportStructureResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            view = await update_exam_import_structure(
                session,
                import_id=import_id,
                sections=_to_sections(payload.sections) if payload.sections is not None else None,
                stimuli=_to_stimuli(payload.stimuli) if payload.stimuli is not None else None,
                expected_total_points=payload.expected_total_points,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(_structure_to_response(view)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except ExamImportNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ExamImportInvalidStateError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=409,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )


@router.post("/{import_id}/approve", status_code=status.HTTP_200_OK, response_model=ExamImportStructureResponse)
async def approve_exam_import_endpoint(
    import_id: uuid.UUID,
    payload: ApproveExamImportRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> ExamImportStructureResponse | JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth
    if role_err := _require_role(auth, request, correlation_id):
        return role_err

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            view = await approve_exam_import(
                session,
                tenant_id=auth.tenant_id,
                import_id=import_id,
                actor_id=auth.user_id,
                require_answer_keys=payload.require_answer_keys,
                generate_branded_pdf=payload.generate_branded_pdf,
                banner_text=payload.banner_text,
                correlation_id=correlation_id,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(_structure_to_response(view)),
                headers={"X-Correlation-ID": correlation_id},
            )
    except ExamImportNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except BrandedPdfRightsDeniedError as exc:
        return _problem_response(
            code=PlatformErrorCode.RIGHTS_RESTRICTED,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ExamImportInvalidStateError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=409,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ExamImportValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
