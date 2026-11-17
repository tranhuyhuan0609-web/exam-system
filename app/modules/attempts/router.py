"""FastAPI /api/v1 routes for C06 attempts: Fast Autosave of one answer and the resync of an attempt (H-04).

The tenant and the membership come only from the signed JWT; the routes run as `ulp_exam_session_rw`.

Errors are RFC 7807 problems whose `error_code` is the H-04 code itself (`PAYLOAD_HASH_MISMATCH`,
`ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED`, `ATTEMPT_DEADLINE_EXPIRED`, `OPTIMISTIC_VERSION_CONFLICT`,
`STALE_CLIENT_SEQUENCE`): the browser queue (`frontend/src/lib/exam-autosave-store.ts`) branches on it, and on
`current_response_version` / `current_sequence` to resynchronise.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict

from app.contracts.autosave import AutosaveRequestContract, AutosaveResponseContract
from app.modules.attempts.internal.autosave import AutosaveResult
from app.modules.attempts.internal.service import AttemptNotFoundError, get_sync_state, save_response
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["attempts"])

IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)]

# H-04 outcome -> the catalogue code whose title and type describe it best
_PROBLEM_BASE: dict[str, PlatformErrorCode] = {
    "PAYLOAD_HASH_MISMATCH": PlatformErrorCode.PAYLOAD_HASH_MISMATCH,
    "STALE_CLIENT_SEQUENCE": PlatformErrorCode.STALE_CLIENT_SEQUENCE,
    "ATTEMPT_DEADLINE_EXPIRED": PlatformErrorCode.ATTEMPT_DEADLINE_EXPIRED,
    "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED": PlatformErrorCode.ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED,
    "OPTIMISTIC_VERSION_CONFLICT": PlatformErrorCode.OPTIMISTIC_VERSION_CONFLICT,
}
_DETAIL = {
    "PAYLOAD_HASH_MISMATCH": "payload_hash does not match the payload, or the Idempotency-Key was already used "
    "for another payload or question",
    "STALE_CLIENT_SEQUENCE": "client_sequence must be greater than the last committed one",
    "ATTEMPT_DEADLINE_EXPIRED": "the deadline of the attempt has passed",
    "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED": "the attempt is no longer in progress",
    "OPTIMISTIC_VERSION_CONFLICT": "expected_response_version is not the current version of this answer: resync",
}


class SavedResponseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_no: int
    current_sequence: int
    last_idempotency_key: str | None
    response_payload: dict[str, Any]
    is_flagged: bool
    updated_at: datetime


class SyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: uuid.UUID
    status: str
    deadline_at: datetime
    server_time: datetime
    responses: dict[str, SavedResponseItem]


def _problem(
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    *,
    status_override: int | None = None,
    error_code: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return problem_json(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
        error_code=error_code,
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    return actor, correlation_id, None


def _outcome_problem(result: AutosaveResult, request: Request, correlation_id: str) -> JSONResponse:
    extensions: dict[str, Any] = {"reason": result.code}
    for name in ("current_response_version", "current_sequence", "attempt_status", "deadline_at"):
        value = getattr(result, name)
        if value is not None:
            extensions[name] = value
    return _problem(
        _PROBLEM_BASE.get(result.code, PlatformErrorCode.VALIDATION_ERROR),
        _DETAIL.get(result.code, result.code),
        request,
        correlation_id,
        status_override=result.http_status,
        error_code=result.code,
        extensions=extensions,
    )


@router.put("/attempts/{attempt_id}/responses/{question_version_id}", response_model=AutosaveResponseContract)
async def put_response(
    attempt_id: uuid.UUID,
    question_version_id: uuid.UUID,
    body: AutosaveRequestContract,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    credentials: OptionalBearer = None,
) -> Any:
    """Save one answer: replayed for a known Idempotency-Key, refused on any conflict, committed otherwise."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    if idempotency_key is not None and idempotency_key != body.idempotency_key:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            "the Idempotency-Key header and the body's idempotency_key differ",
            request,
            correlation_id,
        )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.EXAM_SESSION_RW) as session:
            result = await save_response(
                session, attempt_id, question_version_id, membership_id=actor.membership_id, request=body
            )
    except AttemptNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    if not result.ok:
        return _outcome_problem(result, request, correlation_id)
    assert result.committed_sequence is not None and result.saved_response_version is not None
    assert result.server_timestamp is not None  # an ok result always carries them
    return AutosaveResponseContract(
        status="IDEMPOTENT_REPLAY" if result.code == "IDEMPOTENT_REPLAY" else "COMMITTED",
        attempt_id=attempt_id,
        question_version_id=question_version_id,
        committed_sequence=result.committed_sequence,
        saved_response_version=result.saved_response_version,
        server_timestamp=result.server_timestamp,
    )


@router.get("/attempts/{attempt_id}/sync", response_model=SyncResponse)
async def get_sync(attempt_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> Any:
    """The server's version of every saved answer of the caller's attempt, for the offline queue to resync."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.EXAM_SESSION_RW) as session:
            state = await get_sync_state(session, attempt_id, membership_id=actor.membership_id)
    except AttemptNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    return SyncResponse(
        attempt_id=state.attempt_id,
        status=state.status,
        deadline_at=state.deadline_at,
        server_time=state.server_time,
        responses={
            str(r.question_version_id): SavedResponseItem(
                revision_no=r.revision_no,
                current_sequence=r.current_sequence,
                last_idempotency_key=r.last_idempotency_key,
                response_payload=r.response_payload,
                is_flagged=r.is_flagged,
                updated_at=r.updated_at,
            )
            for r in state.responses
        },
    )
