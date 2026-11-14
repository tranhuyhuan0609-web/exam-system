"""FastAPI /api/v1/admin routes for G01 feature flags and platform settings.

Flags and settings are platform-wide, so only platform administrators may change them; tenant
administrators may read the flags. Every change is audited through the outbox (see internal.service).
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.config.internal.service import (
    FeatureFlagRecord,
    list_feature_flags,
    rule_from_row,
    update_feature_flag,
    update_platform_setting,
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

router = APIRouter(prefix="/api/v1/admin", tags=["admin-config"])

PLATFORM_ADMIN_ROLES = frozenset({"PLATFORM_ADMIN", "SUPER_ADMIN"})
READER_ROLES = PLATFORM_ADMIN_ROLES | {"ADMIN", "TENANT_ADMIN"}
KEY_PATTERN = r"^[a-z0-9][a-z0-9_.:-]{0,99}$"
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class FeatureFlagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    is_enabled: bool
    tenant_whitelist: list[uuid.UUID]
    tenant_blacklist: list[str]
    rollout_percent: int
    pinned_parser_version: str | None
    pinned_ai_model: str | None
    config_json: dict[str, Any]
    updated_at: datetime


class FeatureFlagListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FeatureFlagResponse]


class FeatureFlagPatchRequest(BaseModel):
    """Fields left out keep their value; `pinned_*` set to null clear the pin."""

    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    rollout_percent: int | None = Field(default=None, ge=0, le=100)
    tenant_whitelist: list[uuid.UUID] | None = Field(default=None, max_length=1000)
    tenant_blacklist: list[uuid.UUID] | None = Field(default=None, max_length=1000)
    pinned_parser_version: str | None = Field(default=None, max_length=40)
    pinned_ai_model: str | None = Field(default=None, max_length=120)
    config_json: dict[str, Any] | None = None


class FeatureFlagPatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag: FeatureFlagResponse
    created: bool
    audit_event_id: uuid.UUID


class PlatformSettingPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value_json: Any = Field(...)


class PlatformSettingPatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value_json: Any
    updated_at: datetime
    created: bool
    audit_event_id: uuid.UUID


def _problem(code: PlatformErrorCode, detail: str, request: Request, correlation_id: str) -> JSONResponse:
    problem = build_problem_detail(code=code, detail=detail, instance=request.url.path, correlation_id=correlation_id)
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, allowed_roles: frozenset[str]
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    try:
        actor = decode_and_verify_actor_token(
            extract_bearer_token(raw_auth), secret=get_settings().secret_key, now_epoch=int(time.time())
        )
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if not ({r.upper() for r in actor.roles} & allowed_roles):
        return (
            None,
            correlation_id,
            _problem(PlatformErrorCode.FORBIDDEN, "administrator role required", request, correlation_id),
        )
    return actor, correlation_id, None


def _flag_response(record: FeatureFlagRecord) -> FeatureFlagResponse:
    rule = rule_from_row(record.key, record.is_enabled, record.tenant_whitelist, record.config_json)
    return FeatureFlagResponse(
        key=record.key,
        is_enabled=record.is_enabled,
        tenant_whitelist=list(record.tenant_whitelist),
        tenant_blacklist=sorted(rule.tenant_blacklist),
        rollout_percent=rule.rollout_percent,
        pinned_parser_version=rule.pinned_parser_version,
        pinned_ai_model=rule.pinned_ai_model,
        config_json=dict(rule.config_json),
        updated_at=record.updated_at,
    )


def _valid_key(key: str) -> bool:
    return re.fullmatch(KEY_PATTERN, key) is not None


@router.get("/feature-flags", response_model=FeatureFlagListResponse)
async def get_feature_flags(request: Request, credentials: OptionalBearer = None) -> Any:
    actor, _, err = _authenticate(request, credentials, READER_ROLES)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        flags = await list_feature_flags(session)
    return FeatureFlagListResponse(items=[_flag_response(f) for f in flags])


@router.patch("/feature-flags/{key}", response_model=FeatureFlagPatchResponse)
async def patch_feature_flag(
    key: str, body: FeatureFlagPatchRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    actor, correlation_id, err = _authenticate(request, credentials, PLATFORM_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    if not _valid_key(key):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR, f"flag key must match {KEY_PATTERN}", request, correlation_id
        )
    sent = body.model_fields_set
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await update_feature_flag(
                session,
                key,
                actor_tenant_id=actor.tenant_id,
                actor_user_id=actor.user_id,
                correlation_id=correlation_id,
                is_enabled=body.is_enabled,
                rollout_percent=body.rollout_percent,
                tenant_whitelist=body.tenant_whitelist,
                tenant_blacklist=body.tenant_blacklist,
                pinned_parser_version=body.pinned_parser_version if "pinned_parser_version" in sent else ...,
                pinned_ai_model=body.pinned_ai_model if "pinned_ai_model" in sent else ...,
                config_json=body.config_json,
            )
    except ValueError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id)
    return FeatureFlagPatchResponse(
        flag=_flag_response(result.flag), created=result.created, audit_event_id=result.audit_event_id
    )


@router.patch("/config/{key}", response_model=PlatformSettingPatchResponse)
async def patch_platform_setting(
    key: str, body: PlatformSettingPatchRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    actor, correlation_id, err = _authenticate(request, credentials, PLATFORM_ADMIN_ROLES)
    if err is not None:
        return err
    assert actor is not None
    if not _valid_key(key):
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR, f"setting key must match {KEY_PATTERN}", request, correlation_id
        )
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        result = await update_platform_setting(
            session,
            key,
            body.value_json,
            actor_tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            correlation_id=correlation_id,
        )
    return PlatformSettingPatchResponse(
        key=result.setting.key,
        value_json=result.setting.value_json,
        updated_at=result.setting.updated_at,
        created=result.created,
        audit_event_id=result.audit_event_id,
    )
