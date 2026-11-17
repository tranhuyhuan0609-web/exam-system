"""FastAPI /api/v1 routes for C02: verify a question candidate or a C01 question version with the SymPy sandbox,
read a verification run.

`POST /question-versions/{id}/verify` compares with the version's stored SHORT_NUMERIC answer unless the request
gives `source_answer`; the answer itself is never returned.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.quality.internal.service import (
    VerificationConflictError,
    VerificationNotFoundError,
    get_verification_run,
    verify_candidate_numeric,
    verify_question_version_numeric,
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

router = APIRouter(prefix="/api/v1", tags=["quality"])


VERIFY_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=500)
    source_answer: str = Field(min_length=1, max_length=100)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, le=1)


def _problem(
    request: Request,
    correlation_id: str,
    code: PlatformErrorCode,
    detail: str,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return problem_json(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, *, write: bool
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(request, correlation_id, PlatformErrorCode.UNAUTHORIZED, str(exc))
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(request, correlation_id, PlatformErrorCode.FORBIDDEN, str(exc))
    if write and not ({r.upper() for r in actor.roles} & VERIFY_ROLES):
        return (
            None,
            correlation_id,
            _problem(
                request, correlation_id, PlatformErrorCode.FORBIDDEN, "a content role is required to run a verification"
            ),
        )
    return actor, correlation_id, None


@router.post("/candidates/{candidate_id}/verify", status_code=status.HTTP_201_CREATED)
async def verify_candidate_endpoint(
    candidate_id: uuid.UUID, request: Request, credentials: OptionalBearer = None
) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = VerifyRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            run, candidate_status = await verify_candidate_numeric(
                session,
                candidate_id,
                tenant_id=actor.tenant_id,
                expression=payload.expression,
                source_answer=payload.source_answer,
                tolerance=payload.tolerance,
            )
    except VerificationNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except VerificationConflictError as exc:
        return _problem(
            request,
            correlation_id,
            PlatformErrorCode.VALIDATION_ERROR,
            str(exc),
            status_override=status.HTTP_409_CONFLICT,
            extensions={"reason": exc.code},
        )
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder({"run": run, "candidate_verification_status": candidate_status}),
        headers={"X-Correlation-Id": correlation_id},
    )


class VerifyVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=500)
    source_answer: str | None = Field(default=None, min_length=1, max_length=100)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, le=1)


@router.post("/question-versions/{version_id}/verify", status_code=status.HTTP_201_CREATED)
async def verify_question_version_endpoint(
    version_id: uuid.UUID, request: Request, credentials: OptionalBearer = None
) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = VerifyVersionRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            run, question_status = await verify_question_version_numeric(
                session,
                version_id,
                tenant_id=actor.tenant_id,
                expression=payload.expression,
                source_answer=payload.source_answer,
                tolerance=payload.tolerance,
            )
    except VerificationNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder({"run": run, "question_verification_status": question_status}),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/verification-runs/{run_id}")
async def get_run_endpoint(run_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=False)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        run = await get_verification_run(session, run_id)
    if run is None:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, "verification run not found")
    return JSONResponse(content=jsonable_encoder(run), headers={"X-Correlation-Id": correlation_id})
