"""FastAPI /api/v1 routes for G06 Vertical Packs & Localization Bundles."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.vertical_packs.internal.service import (
    VerticalPackNotFoundError,
    VerticalPackView,
    activate_vertical_pack,
    deactivate_vertical_pack,
    list_vertical_packs,
    register_vertical_pack,
    resolve_localized_ui_label,
    upsert_locale_bundle,
)
from app.modules.vertical_packs.internal.verticals import VerticalPackError
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.roles import is_platform_admin
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1/vertical-packs", tags=["vertical-packs"])



class RegisterVerticalPackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=255)
    taxonomy_root_prefix: str = Field(min_length=1, max_length=60)
    assessment_framework_code: str = Field(min_length=1, max_length=80)
    scoring_policy_code: str = Field(min_length=1, max_length=80)
    default_locale: str = Field(default="vi-VN", max_length=20)
    is_core_k12: bool = False
    is_enabled: bool = False
    extra_manifest: dict[str, Any] | None = None


class UpsertLocaleBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale_code: str = Field(min_length=1, max_length=20)
    namespace: str = Field(default="common", min_length=1, max_length=60)
    messages: dict[str, str]


class VerticalPackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    taxonomy_root_prefix: str
    assessment_framework_code: str
    scoring_policy_code: str
    default_locale: str
    is_core_k12: bool
    is_enabled: bool
    manifest_json: dict[str, Any]


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
    return actor


def _require_admin(actor: ActorContext, request: Request, correlation_id: str) -> JSONResponse | None:
    if not is_platform_admin(actor.roles):  # packs and bundles are platform catalogues, the same for every tenant
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Only a platform administrator may manage vertical packs or locale bundles",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return None


def _serialize_pack(view: VerticalPackView) -> VerticalPackResponse:
    return VerticalPackResponse(
        code=view.code,
        name=view.name,
        taxonomy_root_prefix=view.taxonomy_root_prefix,
        assessment_framework_code=view.assessment_framework_code,
        scoring_policy_code=view.scoring_policy_code,
        default_locale=view.default_locale,
        is_core_k12=view.is_core_k12,
        is_enabled=view.is_enabled,
        manifest_json=view.manifest_json,
    )


@router.get("")
async def list_vertical_packs_endpoint(
    request: Request,
    active_only: bool = False,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        packs = await list_vertical_packs(session, active_only=active_only)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"items": [jsonable_encoder(_serialize_pack(p)) for p in packs]},
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("")
async def register_vertical_pack_endpoint(
    payload: RegisterVerticalPackRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    denied = _require_admin(actor, request, correlation_id)
    if denied is not None:
        return denied

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            pack = await register_vertical_pack(
                session,
                code=payload.code,
                name=payload.name,
                taxonomy_root_prefix=payload.taxonomy_root_prefix,
                assessment_framework_code=payload.assessment_framework_code,
                scoring_policy_code=payload.scoring_policy_code,
                default_locale=payload.default_locale,
                is_core_k12=payload.is_core_k12,
                is_enabled=payload.is_enabled,
                extra_manifest=payload.extra_manifest,
            )
    except VerticalPackError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_pack(pack)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{code}/activate")
async def activate_vertical_pack_endpoint(
    code: str,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    denied = _require_admin(actor, request, correlation_id)
    if denied is not None:
        return denied

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            pack = await activate_vertical_pack(session, code=code)
    except VerticalPackNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except VerticalPackError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_pack(pack)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{code}/deactivate")
async def deactivate_vertical_pack_endpoint(
    code: str,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    denied = _require_admin(actor, request, correlation_id)
    if denied is not None:
        return denied

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            pack = await deactivate_vertical_pack(session, code=code)
    except VerticalPackNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except VerticalPackError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_pack(pack)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.put("/locales")
async def upsert_locale_bundle_endpoint(
    payload: UpsertLocaleBundleRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    denied = _require_admin(actor, request, correlation_id)
    if denied is not None:
        return denied

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            bundle = await upsert_locale_bundle(
                session,
                locale_code=payload.locale_code,
                namespace=payload.namespace,
                messages=payload.messages,
            )
    except VerticalPackError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(bundle),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/labels/resolve")
async def resolve_label_endpoint(
    key: str,
    locale: str,
    request: Request,
    namespace: str = "common",
    default_locale: str = "vi-VN",
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        resolved = await resolve_localized_ui_label(
            session,
            key=key,
            requested_locale=locale,
            namespace=namespace,
            default_locale=default_locale,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(resolved),
        headers={"X-Correlation-ID": correlation_id},
    )
