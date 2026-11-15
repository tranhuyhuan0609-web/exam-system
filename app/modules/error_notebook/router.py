"""FastAPI /api/v1 routes for D05 Error Notebook & Two-Streak Remediation."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.error_notebook.internal.service import (
    ErrorNotebookNotFoundError,
    ErrorNotebookPolicyError,
    ErrorNotebookValidationError,
    ErrorNoteView,
    RemediationTaskView,
    assert_can_mark_resolved,
    create_remediation_task,
    list_error_notes,
    submit_remediation_attempt,
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

router = APIRouter(prefix="/api/v1/error-notes", tags=["error-notebook"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class CreateRemediationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=3, ge=1, le=10)


class ResolveErrorNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_correct: bool | None = None
    at: datetime | None = None


class ErrorNoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    question_version_id: uuid.UUID
    primary_taxonomy_node_id: uuid.UUID | None
    wrong_count: int
    consecutive_correct_count: int
    status: str
    last_wrong_at: datetime
    resolved_at: datetime | None


class RemediationTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    error_note_id: uuid.UUID
    recommended_question_version_ids: list[uuid.UUID]
    completed_count: int
    status: str
    created_at: datetime
    note_status: str


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        status_override=status_code,
        instance=instance,
        correlation_id=correlation_id,
        extensions=extensions,
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
    return actor


def _is_staff(actor: ActorContext) -> bool:
    return bool({r.upper() for r in actor.roles} & _STAFF_ROLES)


def _serialize_note(note: ErrorNoteView) -> ErrorNoteResponse:
    return ErrorNoteResponse(
        id=note.id,
        tenant_id=note.tenant_id,
        membership_id=note.membership_id,
        question_version_id=note.question_version_id,
        primary_taxonomy_node_id=note.primary_taxonomy_node_id,
        wrong_count=note.wrong_count,
        consecutive_correct_count=note.consecutive_correct_count,
        status=note.status,
        last_wrong_at=note.last_wrong_at,
        resolved_at=note.resolved_at,
    )


def _serialize_task(task: RemediationTaskView) -> RemediationTaskResponse:
    return RemediationTaskResponse(
        id=task.id,
        tenant_id=task.tenant_id,
        error_note_id=task.error_note_id,
        recommended_question_version_ids=list(task.recommended_question_version_ids),
        completed_count=task.completed_count,
        status=task.status,
        created_at=task.created_at,
        note_status=task.note_status,
    )


@router.get("", response_model=list[ErrorNoteResponse])
async def list_error_notes_endpoint(
    request: Request,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    primary_taxonomy_node_id: Annotated[uuid.UUID | None, Query()] = None,
    membership_id: Annotated[uuid.UUID | None, Query()] = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    target_membership = membership_id or auth.membership_id
    if target_membership != auth.membership_id and not _is_staff(auth):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="learners can only read their own error notes",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            notes = await list_error_notes(
                session,
                tenant_id=auth.tenant_id,
                membership_id=target_membership,
                status=status_filter,
                primary_taxonomy_node_id=primary_taxonomy_node_id,
            )
    except ErrorNotebookValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder([_serialize_note(n) for n in notes]),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{error_note_id}/remediate", response_model=RemediationTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_remediation_endpoint(
    error_note_id: uuid.UUID,
    payload: CreateRemediationRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            task = await create_remediation_task(
                session,
                tenant_id=auth.tenant_id,
                error_note_id=error_note_id,
                membership_id=auth.membership_id,
                limit=payload.limit,
            )
    except ErrorNotebookNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ErrorNotebookValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_task(task)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{error_note_id}/resolve", response_model=ErrorNoteResponse)
async def resolve_error_note_endpoint(
    error_note_id: uuid.UUID,
    payload: ResolveErrorNoteRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            if payload.is_correct is None:
                note = await assert_can_mark_resolved(
                    session,
                    tenant_id=auth.tenant_id,
                    error_note_id=error_note_id,
                    membership_id=auth.membership_id,
                )
            else:
                note = await submit_remediation_attempt(
                    session,
                    tenant_id=auth.tenant_id,
                    error_note_id=error_note_id,
                    is_correct=payload.is_correct,
                    membership_id=auth.membership_id,
                    at=payload.at,
                )
    except ErrorNotebookNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ErrorNotebookPolicyError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=exc.detail,
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"domain_code": exc.code},
        )
    except ErrorNotebookValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_note(note)),
        headers={"X-Correlation-ID": correlation_id},
    )
