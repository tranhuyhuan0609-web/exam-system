"""FastAPI /api/v1 routes for F01 Groups, Classes & Soft-Delete Roster."""

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
from app.modules.classes.internal.roster import ClassMemberStatus, ClassRole, ClassroomPolicyError
from app.modules.classes.internal.service import (
    ClassConflictError,
    ClassMemberView,
    ClassNotFoundError,
    ClassValidationError,
    ClassView,
    GroupView,
    add_class_member,
    create_class,
    create_group,
    get_class,
    get_class_roster,
    list_groups,
    remove_class_member,
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

router = APIRouter(prefix="/api/v1", tags=["classes"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class CreateGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class GroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    created_at: datetime


class CreateClassRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    group_id: uuid.UUID | None = None
    teacher_membership_id: uuid.UUID | None = None
    academic_year: str | None = Field(default=None, max_length=30)
    invite_code: str | None = Field(default=None, max_length=30)


class ClassResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    group_id: uuid.UUID | None
    teacher_membership_id: uuid.UUID
    name: str
    academic_year: str | None
    invite_code: str | None
    status: str
    created_at: datetime


class AddClassMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: uuid.UUID
    role_in_class: ClassRole = ClassRole.STUDENT


class ClassMemberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    class_id: uuid.UUID
    membership_id: uuid.UUID
    role_in_class: str
    status: str
    joined_at: datetime
    left_at: datetime | None


class ClassDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classroom: ClassResponse
    members: list[ClassMemberResponse]
    active_student_count: int


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
    *,
    staff_only: bool = False,
) -> ActorContext | JSONResponse:
    settings = get_settings()
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    if not raw_auth and request.cookies.get("ulp_access_token"):
        raw_auth = f"Bearer {request.cookies['ulp_access_token']}"
    try:
        token = extract_bearer_token(raw_auth)
        actor = decode_and_verify_actor_token(token, secret=settings.secret_key, now_epoch=int(time.time()))
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

    if staff_only and not any(role.upper() in _STAFF_ROLES for role in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff or teacher role is required for classroom management",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_group(view: GroupView) -> GroupResponse:
    return GroupResponse(id=view.id, tenant_id=view.tenant_id, name=view.name, created_at=view.created_at)


def _serialize_class(view: ClassView) -> ClassResponse:
    return ClassResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        group_id=view.group_id,
        teacher_membership_id=view.teacher_membership_id,
        name=view.name,
        academic_year=view.academic_year,
        invite_code=view.invite_code,
        status=view.status,
        created_at=view.created_at,
    )


def _serialize_member(view: ClassMemberView) -> ClassMemberResponse:
    return ClassMemberResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        class_id=view.class_id,
        membership_id=view.membership_id,
        role_in_class=view.role_in_class,
        status=view.status,
        joined_at=view.joined_at,
        left_at=view.left_at,
    )


@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group_endpoint(
    payload: CreateGroupRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, staff_only=True)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            created = await create_group(session, tenant_id=auth.tenant_id, name=payload.name)
    except ClassValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_group(created)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/groups", response_model=list[GroupResponse])
async def list_groups_endpoint(
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
        rows = await list_groups(session, tenant_id=auth.tenant_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder([_serialize_group(r) for r in rows]),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/classes", response_model=ClassResponse, status_code=status.HTTP_201_CREATED)
async def create_class_endpoint(
    payload: CreateClassRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, staff_only=True)
    if isinstance(auth, JSONResponse):
        return auth

    teacher_mid = payload.teacher_membership_id or auth.membership_id
    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            created = await create_class(
                session,
                tenant_id=auth.tenant_id,
                teacher_membership_id=teacher_mid,
                name=payload.name,
                group_id=payload.group_id,
                academic_year=payload.academic_year,
                invite_code=payload.invite_code,
            )
    except ClassNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ClassConflictError as exc:
        return _problem_response(
            code=PlatformErrorCode.CONFLICT,
            detail=str(exc),
            status_code=409,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ClassValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_class(created)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/classes/{class_id}", response_model=ClassDetailResponse)
async def get_class_endpoint(
    class_id: uuid.UUID,
    request: Request,
    include_inactive: Annotated[bool, Query()] = True,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            classroom = await get_class(session, tenant_id=auth.tenant_id, class_id=class_id)
            roster = await get_class_roster(
                session, tenant_id=auth.tenant_id, class_id=class_id, include_inactive=include_inactive
            )
    except ClassNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    active_students = sum(
        1 for m in roster if m.status == ClassMemberStatus.ACTIVE.value and m.role_in_class == ClassRole.STUDENT.value
    )
    body = ClassDetailResponse(
        classroom=_serialize_class(classroom),
        members=[_serialize_member(m) for m in roster],
        active_student_count=active_students,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/classes/{class_id}/members", response_model=ClassMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_class_member_endpoint(
    class_id: uuid.UUID,
    payload: AddClassMemberRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, staff_only=True)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            member = await add_class_member(
                session,
                tenant_id=auth.tenant_id,
                class_id=class_id,
                membership_id=payload.membership_id,
                role_in_class=payload.role_in_class,
            )
    except ClassNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ClassValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_member(member)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.delete("/classes/{class_id}/members/{member_id}", response_model=ClassMemberResponse)
async def remove_class_member_endpoint(
    class_id: uuid.UUID,
    member_id: uuid.UUID,
    request: Request,
    left_at: Annotated[datetime | None, Query()] = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, staff_only=True)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            member = await remove_class_member(
                session,
                tenant_id=auth.tenant_id,
                class_id=class_id,
                member_or_membership_id=member_id,
                left_at=left_at,
            )
    except ClassNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except ClassroomPolicyError as exc:
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
        content=jsonable_encoder(_serialize_member(member)),
        headers={"X-Correlation-ID": correlation_id},
    )
