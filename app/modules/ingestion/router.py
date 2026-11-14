"""FastAPI /api/v1 routes for A01 document import submission, status polling, retry, and cancel."""

from __future__ import annotations

import base64
import binascii
import json
import time
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.datastructures import UploadFile

from app.config import get_settings
from app.modules.ingestion.internal.service import (
    DocumentVersionView,
    DuplicateIdempotencyConflictError,
    ImportJobNotFoundError,
    ImportJobView,
    IngestionTransitionError,
    IngestionValidationError,
    cancel_import_job,
    get_import_job,
    retry_import_job,
    submit_import,
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

router = APIRouter(prefix="/api/v1/imports", tags=["ingestion"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class SubmitImportJsonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content_base64: str
    mime_type: str | None = None
    job_type: Literal["EXAM_IMPORT", "DRIVE_SYNC", "WEB_CRAWL", "REPROCESS_EXTRACTION"] = "EXAM_IMPORT"
    source_id: uuid.UUID | None = None
    external_id: str | None = None
    title: str | None = None
    idempotency_key: str | None = None
    max_attempts: int = 3
    requires_pro: bool = False
    start_immediately: bool = True


class RetryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = None


class CancelImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = "Cancelled by user"
    event_id: str | None = None


class PipelineStageRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    stage_name: str
    status: str
    worker_version: str | None
    input_ref: str | None
    output_ref: str | None
    started_at: datetime
    finished_at: datetime | None


class DocumentVersionResponse(BaseModel):
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


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID | None
    document_version_id: uuid.UUID | None
    job_type: str
    job_status: str
    current_stage: str | None
    idempotency_key: str
    attempt_count: int
    last_error_code: str | None
    last_error_detail: str | None
    metrics_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    stages: list[PipelineStageRunResponse]
    document_version: DocumentVersionResponse | None
    dead_letter_count: int


class SubmitImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_duplicate: bool
    idempotent_replay: bool
    job: ImportJobResponse
    document_version: DocumentVersionResponse


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


def _serialize_doc_version(dv: DocumentVersionView) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        id=dv.id,
        tenant_id=dv.tenant_id,
        source_document_id=dv.source_document_id,
        source_id=dv.source_id,
        version_no=dv.version_no,
        raw_asset_id=dv.raw_asset_id,
        checksum_sha256=dv.checksum_sha256,
        page_count=dv.page_count,
        mime_type=dv.mime_type,
        title=dv.title,
        created_at=dv.created_at,
    )


def _serialize_job(job: ImportJobView) -> ImportJobResponse:
    return ImportJobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        source_id=job.source_id,
        document_version_id=job.document_version_id,
        job_type=job.job_type,
        job_status=job.job_status,
        current_stage=job.current_stage,
        idempotency_key=job.idempotency_key,
        attempt_count=job.attempt_count,
        last_error_code=job.last_error_code,
        last_error_detail=job.last_error_detail,
        metrics_json=job.metrics_json,
        created_at=job.created_at,
        updated_at=job.updated_at,
        stages=[
            PipelineStageRunResponse(
                id=s.id,
                stage_name=s.stage_name,
                status=s.status,
                worker_version=s.worker_version,
                input_ref=s.input_ref,
                output_ref=s.output_ref,
                started_at=s.started_at,
                finished_at=s.finished_at,
            )
            for s in job.stages
        ],
        document_version=_serialize_doc_version(job.document_version) if job.document_version else None,
        dead_letter_count=job.dead_letter_count,
    )


async def _extract_import_inputs(request: Request) -> tuple[dict[str, Any], bytes]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise IngestionValidationError("multipart upload requires a 'file' field")
        raw_bytes = await upload.read()
        source_id_raw = form.get("source_id")
        max_attempts_raw = form.get("max_attempts")
        requires_pro_raw = form.get("requires_pro")
        start_immediately_raw = form.get("start_immediately")
        params: dict[str, Any] = {
            "filename": str(form.get("filename") or upload.filename or ""),
            "mime_type": str(form.get("mime_type")) if form.get("mime_type") else None,
            "job_type": str(form.get("job_type") or "EXAM_IMPORT"),
            "source_id": uuid.UUID(str(source_id_raw)) if source_id_raw else None,
            "external_id": str(form.get("external_id")) if form.get("external_id") else None,
            "title": str(form.get("title")) if form.get("title") else None,
            "idempotency_key": str(form.get("idempotency_key")) if form.get("idempotency_key") else None,
            "max_attempts": int(str(max_attempts_raw)) if max_attempts_raw else 3,
            "requires_pro": str(requires_pro_raw).lower() in ("true", "1", "yes") if requires_pro_raw else False,
            "start_immediately": (
                str(start_immediately_raw).lower() not in ("false", "0", "no") if start_immediately_raw else True
            ),
        }
        return params, raw_bytes

    try:
        raw_json = await request.json()
        parsed = SubmitImportJsonRequest.model_validate(raw_json)
        raw_bytes = base64.b64decode(parsed.content_base64, validate=True)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, binascii.Error, ValueError) as exc:
        raise IngestionValidationError(f"invalid import request payload: {exc}") from exc

    return {
        "filename": parsed.filename,
        "mime_type": parsed.mime_type,
        "job_type": parsed.job_type,
        "source_id": parsed.source_id,
        "external_id": parsed.external_id,
        "title": parsed.title,
        "idempotency_key": parsed.idempotency_key,
        "max_attempts": parsed.max_attempts,
        "requires_pro": parsed.requires_pro,
        "start_immediately": parsed.start_immediately,
    }, raw_bytes


@router.post("")
async def post_submit_import(
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    try:
        params, raw_bytes = await _extract_import_inputs(request)
    except (IngestionValidationError, ValueError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    idem_header = request.headers.get("idempotency-key")
    eff_idem_key = params["idempotency_key"] or (idem_header.strip() if idem_header else None)

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await submit_import(
                session,
                tenant_id=actor.tenant_id,
                raw_stream=raw_bytes,
                filename=params["filename"],
                mime_type=params["mime_type"],
                job_type=params["job_type"],
                source_id=params["source_id"],
                external_id=params["external_id"],
                title=params["title"],
                idempotency_key=eff_idem_key,
                max_attempts=params["max_attempts"],
                requires_pro=params["requires_pro"],
                start_immediately=params["start_immediately"],
                correlation_id=correlation_id,
            )
    except DuplicateIdempotencyConflictError as exc:
        return _problem_response(
            code=PlatformErrorCode.PAYLOAD_HASH_MISMATCH,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except IngestionValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    payload = SubmitImportResponse(
        is_duplicate=result.is_duplicate,
        idempotent_replay=result.idempotent_replay,
        job=_serialize_job(result.job),
        document_version=_serialize_doc_version(result.document_version),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if result.is_duplicate else status.HTTP_201_CREATED,
        content=jsonable_encoder(payload),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/{job_id}")
async def get_import_status(
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
            job = await get_import_job(session, tenant_id=actor.tenant_id, job_id=job_id)
    except ImportJobNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_job(job)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/{job_id}/retry")
async def post_retry_import(
    job_id: uuid.UUID,
    request: Request,
    body: RetryImportRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    req_body = body or RetryImportRequest()
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            job = await retry_import_job(
                session,
                tenant_id=actor.tenant_id,
                job_id=job_id,
                event_id=req_body.event_id,
                correlation_id=correlation_id,
            )
    except ImportJobNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except IngestionTransitionError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            extensions={"transition_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_job(job)),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/{job_id}/cancel")
async def post_cancel_import(
    job_id: uuid.UUID,
    request: Request,
    body: CancelImportRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    req_body = body or CancelImportRequest()
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            job = await cancel_import_job(
                session,
                tenant_id=actor.tenant_id,
                job_id=job_id,
                reason=req_body.reason,
                event_id=req_body.event_id,
                correlation_id=correlation_id,
            )
    except ImportJobNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except IngestionTransitionError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
            extensions={"transition_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_job(job)),
        headers={"X-Correlation-Id": correlation_id},
    )
