"""FastAPI /api/v1 routes for A05 document registration, version listing, extraction, and reprocess."""

from __future__ import annotations

import base64
import binascii
import json
import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile

from app.config import get_settings
from app.modules.ingestion.public import DuplicateIdempotencyConflictError
from app.modules.parsers.internal.routing import DocumentXmlTooLargeError, ParserRoutingError
from app.modules.parsers.internal.service import (
    DocumentNotFoundError,
    DocumentUploadView,
    DocumentVersionSummary,
    ExtractionRunNotFoundError,
    ExtractionRunView,
    ParserValidationError,
    list_document_versions,
    register_and_ingest_document,
    reprocess_extraction_run,
    start_document_extraction,
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

router = APIRouter(prefix="/api/v1", tags=["parsers"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class RegisterDocumentJsonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content_base64: str
    mime_type: str | None = None
    source_id: uuid.UUID | None = None
    external_id: str | None = None
    title: str | None = None
    idempotency_key: str | None = None
    requires_pro: bool = False


class StartExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: uuid.UUID | None = None
    profile_code: str | None = Field(default=None, max_length=60)
    ocr_engine_version: str | None = Field(default=None, max_length=40)
    acquisition_job_id: uuid.UUID | None = None


class ReprocessExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[int] = Field(min_length=1)
    profile_code: str | None = Field(default=None, max_length=60)
    ocr_engine_version: str | None = Field(default=None, max_length=40)


class DocumentUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_id: uuid.UUID
    version_no: int
    raw_asset_id: uuid.UUID
    acquisition_job_id: uuid.UUID
    checksum_sha256: str
    mime_type: str
    page_count: int | None
    deduplicated: bool
    routing_strategy: str
    routing_reasons: list[str]
    detected_markers: list[str]


class DocumentVersionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    source_document_id: uuid.UUID
    source_id: uuid.UUID
    version_no: int
    raw_asset_id: uuid.UUID
    checksum_sha256: str
    page_count: int | None
    mime_type: str
    title: str | None
    created_at: datetime


class DocumentVersionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    versions: list[DocumentVersionSummaryResponse]


class ExtractionRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    acquisition_job_id: uuid.UUID | None
    profile_code: str
    parser_version: str
    ocr_engine_version: str | None
    confidence_score: float | None
    silver_artifact_key: str | None
    parent_run_id: uuid.UUID | None
    target_pages: list[int]
    created_at: datetime


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
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


def _serialize_upload(view: DocumentUploadView) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        source_document_id=view.source_document_id,
        document_version_id=view.document_version_id,
        source_id=view.source_id,
        version_no=view.version_no,
        raw_asset_id=view.raw_asset_id,
        acquisition_job_id=view.acquisition_job_id,
        checksum_sha256=view.checksum_sha256,
        mime_type=view.mime_type,
        page_count=view.page_count,
        deduplicated=view.deduplicated,
        routing_strategy=view.routing_strategy,
        routing_reasons=list(view.routing_reasons),
        detected_markers=list(view.detected_markers),
    )


def _serialize_version(view: DocumentVersionSummary) -> DocumentVersionSummaryResponse:
    return DocumentVersionSummaryResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        source_document_id=view.source_document_id,
        source_id=view.source_id,
        version_no=view.version_no,
        raw_asset_id=view.raw_asset_id,
        checksum_sha256=view.checksum_sha256,
        page_count=view.page_count,
        mime_type=view.mime_type,
        title=view.title,
        created_at=view.created_at,
    )


def _serialize_run(view: ExtractionRunView) -> ExtractionRunResponse:
    return ExtractionRunResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        document_version_id=view.document_version_id,
        acquisition_job_id=view.acquisition_job_id,
        profile_code=view.profile_code,
        parser_version=view.parser_version,
        ocr_engine_version=view.ocr_engine_version,
        confidence_score=float(view.confidence_score) if view.confidence_score is not None else None,
        silver_artifact_key=view.silver_artifact_key,
        parent_run_id=view.parent_run_id,
        target_pages=list(view.target_pages),
        created_at=view.created_at,
    )


def _optional_uuid(raw_value: Any, field_name: str) -> uuid.UUID | None:
    if raw_value is None or raw_value == "":
        return None
    try:
        return uuid.UUID(str(raw_value))
    except ValueError as exc:
        raise ParserValidationError(f"{field_name} must be a valid UUID") from exc


@router.post("/documents", status_code=status.HTTP_201_CREATED, response_model=DocumentUploadResponse)
async def register_document_endpoint(
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    content_type = (request.headers.get("content-type") or "").lower()
    raw_bytes: bytes
    filename: str
    mime_type: str | None = None
    source_id: uuid.UUID | None = None
    external_id: str | None = None
    title: str | None = None
    idempotency_key: str | None = request.headers.get("idempotency-key")
    requires_pro = False

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return _problem_response(
                code=PlatformErrorCode.VALIDATION_ERROR,
                detail="multipart field 'file' is required",
                request=request,
                correlation_id=correlation_id,
            )
        filename = str(form.get("filename") or upload.filename or "")
        raw_bytes = await upload.read()
        raw_mime = form.get("mime_type")
        mime_type = str(raw_mime) if raw_mime else None
        raw_ext = form.get("external_id")
        external_id = str(raw_ext) if raw_ext else None
        raw_title = form.get("title")
        title = str(raw_title) if raw_title else None
        raw_idem = form.get("idempotency_key")
        if raw_idem:
            idempotency_key = str(raw_idem)
        requires_pro = str(form.get("requires_pro") or "false").lower() == "true"
        try:
            source_id = _optional_uuid(form.get("source_id"), "source_id")
        except ParserValidationError as exc:
            return _problem_response(
                code=PlatformErrorCode.VALIDATION_ERROR,
                detail=str(exc),
                request=request,
                correlation_id=correlation_id,
            )
    else:
        try:
            raw_json = await request.json()
        except (ValueError, json.JSONDecodeError):
            return _problem_response(
                code=PlatformErrorCode.VALIDATION_ERROR,
                detail="request body must be valid JSON or multipart/form-data",
                request=request,
                correlation_id=correlation_id,
            )
        try:
            payload = RegisterDocumentJsonRequest.model_validate(raw_json)
        except ValidationError as exc:
            return _problem_response(
                code=PlatformErrorCode.VALIDATION_ERROR,
                detail=str(exc),
                request=request,
                correlation_id=correlation_id,
            )
        try:
            raw_bytes = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            return _problem_response(
                code=PlatformErrorCode.VALIDATION_ERROR,
                detail=f"content_base64 is not valid base64: {exc}",
                request=request,
                correlation_id=correlation_id,
            )
        filename = payload.filename
        mime_type = payload.mime_type
        source_id = payload.source_id
        external_id = payload.external_id
        title = payload.title
        idempotency_key = payload.idempotency_key or idempotency_key
        requires_pro = payload.requires_pro

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await register_and_ingest_document(
                session,
                tenant_id=actor.tenant_id,
                raw_bytes=raw_bytes,
                filename=filename,
                mime_type=mime_type,
                source_id=source_id,
                external_id=external_id,
                title=title,
                idempotency_key=idempotency_key,
                requires_pro=requires_pro,
                correlation_id=correlation_id,
            )
    except DocumentXmlTooLargeError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    except DuplicateIdempotencyConflictError as exc:
        return _problem_response(
            code=PlatformErrorCode.PAYLOAD_HASH_MISMATCH,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_409_CONFLICT,
        )
    except (ParserValidationError, ParserRoutingError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_upload(result)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/documents/{document_id}/versions", response_model=DocumentVersionListResponse)
async def list_document_versions_endpoint(
    document_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            versions = await list_document_versions(session, source_document_id=document_id)
    except DocumentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    body = DocumentVersionListResponse(
        document_id=document_id,
        versions=[_serialize_version(v) for v in versions],
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(body),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post(
    "/documents/{document_id}/extract",
    status_code=status.HTTP_201_CREATED,
    response_model=ExtractionRunResponse,
)
async def start_document_extraction_endpoint(
    document_id: uuid.UUID,
    request: Request,
    body: StartExtractionRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    params = body or StartExtractionRequest()
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            run = await start_document_extraction(
                session,
                tenant_id=actor.tenant_id,
                document_id=document_id,
                document_version_id=params.document_version_id,
                profile_code=params.profile_code,
                ocr_engine_version=params.ocr_engine_version,
                acquisition_job_id=params.acquisition_job_id,
                correlation_id=correlation_id,
            )
    except DocumentNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except DocumentXmlTooLargeError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            status_override=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    except (ParserValidationError, ParserRoutingError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_run(run)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post(
    "/extraction-runs/{run_id}/reprocess",
    status_code=status.HTTP_201_CREATED,
    response_model=ExtractionRunResponse,
)
async def reprocess_extraction_run_endpoint(
    run_id: uuid.UUID,
    body: ReprocessExtractionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            run = await reprocess_extraction_run(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                pages=body.pages,
                profile_code=body.profile_code,
                ocr_engine_version=body.ocr_engine_version,
                correlation_id=correlation_id,
            )
    except ExtractionRunNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except (ParserValidationError, ParserRoutingError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_run(run)),
        headers={"X-Correlation-Id": correlation_id},
    )
