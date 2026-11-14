"""FastAPI /api/v1 routes for A06 OCR & Layout Segmentation Pipeline."""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.layout.internal.margins import LayoutProfileConfig
from app.modules.layout.internal.service import (
    ExtractedPageView,
    LayoutRegionView,
    LayoutValidationError,
    OcrJobNotFoundError,
    OcrLayoutJobView,
    get_ocr_layout_job,
    reprocess_ocr_layout_page,
    run_ocr_layout_job,
)
from app.modules.parsers.public import ExtractionRunNotFoundError
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

router = APIRouter(prefix="/api/v1/ocr", tags=["layout"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class CreateOcrJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_run_id: uuid.UUID
    crop_figures: bool = True
    cluster_padding_pt: float = Field(default=10.0, ge=0.0, le=100.0)
    header_band_ratio: float = Field(default=0.065, gt=0.0, le=0.5)
    footer_band_ratio: float = Field(default=0.065, gt=0.0, le=0.5)
    min_figure_width: float = Field(default=35.0, ge=0.0)
    min_figure_height: float = Field(default=30.0, ge=0.0)


class ReprocessPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop_figures: bool = True
    cluster_padding_pt: float = Field(default=10.0, ge=0.0, le=100.0)
    header_band_ratio: float = Field(default=0.065, gt=0.0, le=0.5)
    footer_band_ratio: float = Field(default=0.065, gt=0.0, le=0.5)


class LayoutRegionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    extracted_page_id: uuid.UUID
    region_type: str
    bbox_json: dict[str, Any]
    cropped_asset_id: uuid.UUID | None
    extracted_text: str | None


class ExtractedPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    extraction_run_id: uuid.UUID
    page_no: int
    render_asset_id: uuid.UUID | None
    width_pt: float
    height_pt: float
    detected_header_y0: float | None
    detected_footer_y1: float | None
    raw_text: str | None
    regions: list[LayoutRegionResponse]


class OcrLayoutJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    extraction_run_id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    profile_code: str
    parser_version: str
    pages: list[ExtractedPageResponse]


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

    return actor, correlation_id, None


def _serialize_region(region: LayoutRegionView) -> LayoutRegionResponse:
    return LayoutRegionResponse(
        id=region.id,
        tenant_id=region.tenant_id,
        extracted_page_id=region.extracted_page_id,
        region_type=region.region_type,
        bbox_json=region.bbox_json,
        cropped_asset_id=region.cropped_asset_id,
        extracted_text=region.extracted_text,
    )


def _serialize_page(page: ExtractedPageView) -> ExtractedPageResponse:
    return ExtractedPageResponse(
        id=page.id,
        tenant_id=page.tenant_id,
        extraction_run_id=page.extraction_run_id,
        page_no=page.page_no,
        render_asset_id=page.render_asset_id,
        width_pt=page.width_pt,
        height_pt=page.height_pt,
        detected_header_y0=page.detected_header_y0,
        detected_footer_y1=page.detected_footer_y1,
        raw_text=page.raw_text,
        regions=[_serialize_region(r) for r in page.regions],
    )


def _serialize_job(job: OcrLayoutJobView) -> OcrLayoutJobResponse:
    return OcrLayoutJobResponse(
        job_id=job.job_id,
        extraction_run_id=job.extraction_run_id,
        tenant_id=job.tenant_id,
        document_version_id=job.document_version_id,
        profile_code=job.profile_code,
        parser_version=job.parser_version,
        pages=[_serialize_page(p) for p in job.pages],
    )


@router.post("/jobs", status_code=status.HTTP_201_CREATED, response_model=OcrLayoutJobResponse)
async def create_ocr_layout_job_endpoint(
    body: CreateOcrJobRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    cfg = LayoutProfileConfig(
        header_band_ratio=body.header_band_ratio,
        footer_band_ratio=body.footer_band_ratio,
        min_figure_width=body.min_figure_width,
        min_figure_height=body.min_figure_height,
        cluster_padding_pt=body.cluster_padding_pt,
    )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await run_ocr_layout_job(
                session,
                tenant_id=actor.tenant_id,
                extraction_run_id=body.extraction_run_id,
                config=cfg,
                crop_figures=body.crop_figures,
                correlation_id=correlation_id,
            )
    except (ExtractionRunNotFoundError, OcrJobNotFoundError) as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except LayoutValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_job(result)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/jobs/{job_id}", response_model=OcrLayoutJobResponse)
async def get_ocr_layout_job_endpoint(
    job_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await get_ocr_layout_job(session, extraction_run_id=job_id)
    except OcrJobNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_job(result)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/jobs/{job_id}/pages/{page}/reprocess", response_model=ExtractedPageResponse)
async def reprocess_ocr_page_endpoint(
    job_id: uuid.UUID,
    page: int,
    request: Request,
    body: ReprocessPageRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    params = body or ReprocessPageRequest()
    cfg = LayoutProfileConfig(
        header_band_ratio=params.header_band_ratio,
        footer_band_ratio=params.footer_band_ratio,
        cluster_padding_pt=params.cluster_padding_pt,
    )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            page_view = await reprocess_ocr_layout_page(
                session,
                tenant_id=actor.tenant_id,
                extraction_run_id=job_id,
                page_no=page,
                config=cfg,
                crop_figures=params.crop_figures,
                correlation_id=correlation_id,
            )
    except (OcrJobNotFoundError, ExtractionRunNotFoundError) as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except LayoutValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_page(page_view)),
        headers={"X-Correlation-Id": correlation_id},
    )
