"""FastAPI /api/v1 routes for F03 Class Gradebook, Item Analysis & CSV Export."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.modules.gradebook.internal.service import (
    GradebookNotFoundError,
    GradebookProjectionView,
    GradebookValidationError,
    export_gradebook_csv,
    get_gradebook_projection,
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

router = APIRouter(prefix="/api/v1/classes", tags=["gradebook"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})


class GradebookStudentRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: str
    student_label: str
    score: str
    submitted_at: datetime | None
    due_at: datetime | None
    is_historical_inactive_member: bool


class GradebookProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: uuid.UUID
    assignment_id: uuid.UUID
    tenant_id: uuid.UUID
    stats_json: dict[str, Any]
    top_wrong_questions_json: list[dict[str, Any]]
    rows: list[GradebookStudentRowResponse]
    updated_at: datetime


class ItemAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: uuid.UUID
    assignment_id: uuid.UUID
    tenant_id: uuid.UUID
    top_5_hardest_questions: list[dict[str, Any]]
    updated_at: datetime


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
    *,
    staff_only: bool = True,
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

    if staff_only and not any(role.upper() in _STAFF_ROLES for role in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff or teacher role is required for class gradebook access",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_projection(view: GradebookProjectionView) -> GradebookProjectionResponse:
    return GradebookProjectionResponse(
        class_id=view.class_id,
        assignment_id=view.assignment_id,
        tenant_id=view.tenant_id,
        stats_json=view.stats_json,
        top_wrong_questions_json=view.top_wrong_questions_json,
        rows=[
            GradebookStudentRowResponse(
                membership_id=r.membership_id,
                student_label=r.student_label,
                score=str(r.score),
                submitted_at=r.submitted_at,
                due_at=r.due_at,
                is_historical_inactive_member=r.is_historical_inactive_member,
            )
            for r in view.rows
        ],
        updated_at=view.updated_at,
    )


@router.get("/{class_id}/gradebook", response_model=GradebookProjectionResponse)
async def get_gradebook_endpoint(
    class_id: uuid.UUID,
    assignment_id: Annotated[uuid.UUID, Query()],
    request: Request,
    refresh: Annotated[bool, Query()] = True,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            projection = await get_gradebook_projection(
                session,
                tenant_id=auth.tenant_id,
                class_id=class_id,
                assignment_id=assignment_id,
                refresh=refresh,
            )
    except GradebookNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except GradebookValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_projection(projection)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{class_id}/item-analysis", response_model=ItemAnalysisResponse)
async def get_item_analysis_endpoint(
    class_id: uuid.UUID,
    assignment_id: Annotated[uuid.UUID, Query()],
    request: Request,
    refresh: Annotated[bool, Query()] = True,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            projection = await get_gradebook_projection(
                session,
                tenant_id=auth.tenant_id,
                class_id=class_id,
                assignment_id=assignment_id,
                refresh=refresh,
            )
    except GradebookNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except GradebookValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    body = ItemAnalysisResponse(
        class_id=projection.class_id,
        assignment_id=projection.assignment_id,
        tenant_id=projection.tenant_id,
        top_5_hardest_questions=projection.top_wrong_questions_json,
        updated_at=projection.updated_at,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/{class_id}/gradebook/export")
async def export_gradebook_csv_endpoint(
    class_id: uuid.UUID,
    assignment_id: Annotated[uuid.UUID, Query()],
    request: Request,
    credentials: OptionalBearer = None,
) -> Response:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            csv_body = await export_gradebook_csv(
                session, tenant_id=auth.tenant_id, class_id=class_id, assignment_id=assignment_id
            )
    except GradebookNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except GradebookValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="gradebook-{class_id}-{assignment_id}.csv"',
            "X-Correlation-ID": correlation_id,
        },
    )
