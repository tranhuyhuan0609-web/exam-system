"""FastAPI /api/v1 routes for A03 Google Drive Sync."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.infrastructure.ai_solver import AiSolver
from app.modules.drive_sync.internal.auto_ingest import fetch_and_ingest_folder
from app.modules.drive_sync.internal.service import (
    DriveConnectionView,
    DriveFileChange,
    DriveSyncJobView,
    DriveSyncNotFoundError,
    DriveSyncPolicyError,
    DriveSyncValidationError,
    FilePairingView,
    confirm_or_reject_file_pairing,
    connect_google_drive,
    get_drive_connection,
    get_drive_sync_job,
    revoke_drive_connection,
    sync_drive_connection,
)
from app.modules.questions.public import default_numeric_checker
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["drive-sync"])

# Drive sync fills the question bank and the library: authors, reviewers and administrators only.
_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "REVIEWER", "CONTENT_AUTHOR"})


class ConnectGoogleDriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    secret_vault_ref: str = Field(min_length=3, max_length=255)
    granted_scopes: list[str] = Field(default_factory=lambda: ["https://www.googleapis.com/auth/drive.file"])
    provider: str = Field(default="GOOGLE_DRIVE", max_length=40)
    base_uri: str | None = Field(default=None, max_length=512)
    rights_status: str = Field(default="PENDING_REVIEW", max_length=40)
    initial_cursor_token: str = Field(default="cursor:init", min_length=1, max_length=512)
    source_id: uuid.UUID | None = None


class DriveChangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drive_file_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    mime_type: str = Field(min_length=1, max_length=120)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    object_key: str = Field(min_length=1, max_length=512)
    folder_id: str | None = Field(default=None, max_length=255)
    folder_path: str = Field(default="/", max_length=512)
    removed: bool = False
    subject_code: str | None = Field(default=None, max_length=120)
    question_count: int | None = Field(default=None, ge=0)
    rights_status: str | None = Field(default=None, max_length=40)
    metadata: dict[str, Any] | None = None


class SyncDriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_cursor_token: str = Field(min_length=1, max_length=512)
    changes: list[DriveChangePayload] = Field(default_factory=list)
    auto_pair: bool = True


class ConfirmPairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["CONFIRMED", "REJECTED"] = "CONFIRMED"


class DriveConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    provider: str
    secret_vault_ref: str
    granted_scopes: list[str]
    status: str
    cursor_token: str | None
    last_synced_at: datetime | None
    created_at: datetime


class FilePairingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    exam_doc_id: uuid.UUID
    solution_doc_id: uuid.UUID
    similarity_score: Decimal
    signals_json: dict[str, Any]
    status: str
    created_at: datetime


class DriveSyncJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    source_id: uuid.UUID
    job_status: str
    cursor_token: str
    ingested_count: int
    renamed_or_moved_count: int
    skipped_count: int
    pairings_created: list[FilePairingResponse]
    created_at: datetime


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
    if not any(r.upper() in _STAFF_ROLES for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff or admin role required for Google Drive integrations",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_connection(view: DriveConnectionView) -> DriveConnectionResponse:
    return DriveConnectionResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        source_id=view.source_id,
        provider=view.provider,
        secret_vault_ref=view.secret_vault_ref,
        granted_scopes=list(view.granted_scopes),
        status=view.status,
        cursor_token=view.cursor_token,
        last_synced_at=view.last_synced_at,
        created_at=view.created_at,
    )


def _serialize_pairing(view: FilePairingView) -> FilePairingResponse:
    return FilePairingResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        exam_doc_id=view.exam_doc_id,
        solution_doc_id=view.solution_doc_id,
        similarity_score=view.similarity_score,
        signals_json=view.signals_json,
        status=view.status,
        created_at=view.created_at,
    )


def _serialize_sync_job(view: DriveSyncJobView) -> DriveSyncJobResponse:
    return DriveSyncJobResponse(
        job_id=view.job_id,
        tenant_id=view.tenant_id,
        connection_id=view.connection_id,
        source_id=view.source_id,
        job_status=view.job_status,
        cursor_token=view.cursor_token,
        ingested_count=view.ingested_count,
        renamed_or_moved_count=view.renamed_or_moved_count,
        skipped_count=view.skipped_count,
        pairings_created=[_serialize_pairing(p) for p in view.pairings_created],
        created_at=view.created_at,
    )


@router.post("/integrations/google-drive/connect", status_code=status.HTTP_201_CREATED)
async def connect_google_drive_endpoint(
    payload: ConnectGoogleDriveRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await connect_google_drive(
                session,
                tenant_id=actor.tenant_id,
                name=payload.name,
                secret_vault_ref=payload.secret_vault_ref,
                granted_scopes=payload.granted_scopes,
                provider=payload.provider,
                base_uri=payload.base_uri,
                rights_status=payload.rights_status,
                initial_cursor_token=payload.initial_cursor_token,
                source_id=payload.source_id,
            )
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except DriveSyncPolicyError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )
    except DriveSyncValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"validation_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_connection(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/drive/connections/{connection_id}")
async def get_drive_connection_endpoint(
    connection_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await get_drive_connection(session, tenant_id=actor.tenant_id, connection_id=connection_id)
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_connection(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/drive/connections/{connection_id}/revoke")
async def revoke_drive_connection_endpoint(
    connection_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await revoke_drive_connection(session, tenant_id=actor.tenant_id, connection_id=connection_id)
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_connection(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/drive/connections/{connection_id}/sync", status_code=status.HTTP_201_CREATED)
async def sync_drive_connection_endpoint(
    connection_id: uuid.UUID,
    payload: SyncDriveRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    changes = [
        DriveFileChange(
            drive_file_id=item.drive_file_id,
            title=item.title,
            mime_type=item.mime_type,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            object_key=item.object_key,
            folder_id=item.folder_id,
            folder_path=item.folder_path,
            removed=item.removed,
            subject_code=item.subject_code,
            question_count=item.question_count,
            rights_status=item.rights_status,
            metadata=item.metadata,
        )
        for item in payload.changes
    ]
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await sync_drive_connection(
                session,
                tenant_id=actor.tenant_id,
                connection_id=connection_id,
                next_cursor_token=payload.next_cursor_token,
                changes=changes,
                auto_pair=payload.auto_pair,
            )
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except DriveSyncPolicyError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )
    except DriveSyncValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"validation_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_sync_job(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/drive/sync-jobs/{job_id}")
async def get_drive_sync_job_endpoint(
    job_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await get_drive_sync_job(session, tenant_id=actor.tenant_id, job_id=job_id)
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_sync_job(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/drive/pairings/{pairing_id}/confirm")
async def confirm_drive_pairing_endpoint(
    pairing_id: uuid.UUID,
    payload: ConfirmPairingRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await confirm_or_reject_file_pairing(
                session,
                tenant_id=actor.tenant_id,
                pairing_id=pairing_id,
                decision=payload.decision,
            )
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except DriveSyncValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"validation_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_pairing(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


class FetchAndImportDriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: str = Field(min_length=10, max_length=200)
    mode: Literal["AUTO", "QUESTIONS", "DOCUMENTS"] = "AUTO"
    max_files: int = Field(default=20, ge=1, le=50)


class DriveFileOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drive_file_id: str
    name: str
    kind: str
    reason: str | None
    document_version_id: uuid.UUID | None
    question_ids: list[uuid.UUID]
    duplicate_question_ids: list[uuid.UUID]
    unresolved_questions: int
    content_item_id: uuid.UUID | None


class FetchAndImportDriveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: uuid.UUID
    folder_id: str
    listed: int
    created_question_count: int
    content_item_count: int
    files: list[DriveFileOutcomeResponse]


def get_drive_transport() -> Any:
    """None: the real network. Tests override this dependency with an offline httpx transport."""
    return None


def get_drive_solver() -> AiSolver:
    """The configured AI solver; tests override it."""
    return AiSolver(numeric_checker=default_numeric_checker())


@router.post("/drive/connections/{connection_id}/fetch-and-import", response_model=FetchAndImportDriveResponse)
async def fetch_and_import_drive_endpoint(
    connection_id: uuid.UUID,
    payload: FetchAndImportDriveRequest,
    request: Request,
    credentials: OptionalBearer = None,
    transport: Any = Depends(get_drive_transport),  # noqa: B008
    solver: AiSolver = Depends(get_drive_solver),  # noqa: B008
) -> JSONResponse:
    """Pull the PDFs of a Drive folder: exams into the bank (solved, tagged DRAFTs), documents into the library."""
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    instance = str(request.url.path)
    try:
        # no transaction here: the function opens short ones around the database steps, never around Drive or AI
        result = await fetch_and_ingest_folder(
            tenant_id=actor.tenant_id,
            connection_id=connection_id,
            folder_id=payload.folder_id,
            created_by=actor.user_id,
            solver=solver,
            mode=payload.mode,
            max_files=payload.max_files,
            transport=transport,
        )
    except DriveSyncNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND, detail=str(exc), status_code=404, instance=instance,
            correlation_id=correlation_id,
        )  # fmt: skip
    except DriveSyncPolicyError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN, detail=str(exc), status_code=403, instance=instance,
            correlation_id=correlation_id, extensions={"policy_code": exc.code},
        )  # fmt: skip
    except DriveSyncValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR, detail=str(exc), status_code=422, instance=instance,
            correlation_id=correlation_id, extensions={"validation_code": exc.code},
        )  # fmt: skip
    body = FetchAndImportDriveResponse(
        connection_id=result.connection_id,
        folder_id=result.folder_id,
        listed=result.listed,
        created_question_count=result.created_question_count,
        content_item_count=result.content_item_count,
        files=[
            DriveFileOutcomeResponse(
                drive_file_id=f.drive_file_id,
                name=f.name,
                kind=f.kind,
                reason=f.reason,
                document_version_id=f.document_version_id,
                question_ids=list(f.question_ids),
                duplicate_question_ids=list(f.duplicate_question_ids),
                unresolved_questions=f.unresolved_questions,
                content_item_id=f.content_item_id,
            )
            for f in result.files
        ],
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK, content=jsonable_encoder(body), headers={"X-Correlation-ID": correlation_id}
    )
