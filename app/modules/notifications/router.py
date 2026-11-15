"""FastAPI /api/v1 routes for F06 Inbox Notifications & Preferences."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.modules.notifications.internal.router import NotificationCategory, NotificationChannel
from app.modules.notifications.internal.service import (
    NotificationNotFoundError,
    NotificationValidationError,
    NotificationView,
    PreferencesView,
    get_preferences,
    list_inbox,
    read_notification,
    update_preferences,
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

router = APIRouter(prefix="/api/v1", tags=["notifications"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    notification_id: uuid.UUID
    channel: str
    status: str
    delivered_at: datetime


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    dedup_key: str
    title: str
    body_text: str
    is_read: bool
    created_at: datetime
    deliveries: list[DeliveryResponse]


class InboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NotificationResponse]
    unread_count: int


class PreferencesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    enabled_channels: list[NotificationChannel]
    muted_categories: list[NotificationCategory]


class UpdatePreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_channels: list[NotificationChannel] | None = None
    muted_categories: list[NotificationCategory] | None = None


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
        actor = decode_and_verify_actor_token(token, secret=settings.secret_key, now_epoch=int(time.time()))
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


def _serialize_notification(view: NotificationView) -> NotificationResponse:
    return NotificationResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        membership_id=view.membership_id,
        dedup_key=view.dedup_key,
        title=view.title,
        body_text=view.body_text,
        is_read=view.is_read,
        created_at=view.created_at,
        deliveries=[
            DeliveryResponse(
                id=d.id,
                tenant_id=d.tenant_id,
                notification_id=d.notification_id,
                channel=d.channel,
                status=d.status,
                delivered_at=d.delivered_at,
            )
            for d in view.deliveries
        ],
    )


def _serialize_preferences(view: PreferencesView) -> PreferencesResponse:
    return PreferencesResponse(
        tenant_id=view.tenant_id,
        membership_id=view.membership_id,
        enabled_channels=list(view.enabled_channels),
        muted_categories=list(view.muted_categories),
    )


@router.get("/inbox", response_model=InboxResponse)
async def get_inbox_endpoint(
    request: Request,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
        inbox = await list_inbox(
            session,
            tenant_id=auth.tenant_id,
            membership_id=auth.membership_id,
            unread_only=unread_only,
            limit=limit,
        )

    body = InboxResponse(
        items=[_serialize_notification(item) for item in inbox.items],
        unread_count=inbox.unread_count,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/inbox/{notification_id}/read", response_model=NotificationResponse)
async def mark_read_endpoint(
    notification_id: uuid.UUID,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            updated = await read_notification(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                notification_id=notification_id,
            )
    except NotificationNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_notification(updated)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/notifications/preferences", response_model=PreferencesResponse)
async def get_preferences_endpoint(
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            prefs = await get_preferences(session, tenant_id=auth.tenant_id, membership_id=auth.membership_id)
    except NotificationValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_preferences(prefs)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.patch("/notifications/preferences", response_model=PreferencesResponse)
async def update_preferences_endpoint(
    payload: UpdatePreferencesRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            prefs = await update_preferences(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                enabled_channels=payload.enabled_channels,
                muted_categories=payload.muted_categories,
            )
    except (NotificationValidationError, ValueError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_preferences(prefs)),
        headers={"X-Correlation-ID": correlation_id},
    )
