"""FastAPI /api/v1 routes for B03 asset upload sessions, finalization, and signed download URLs."""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.modules.assets.internal.access import (
    MAX_SIGNED_URL_TTL_SECONDS,
    AssetEntitlementTier,
    AssetNotFoundError,
    AssetVisibility,
    SignedUrlPolicyError,
    UploadValidationError,
    VirusScanStatus,
)
from app.modules.assets.internal.service import (
    AssetView,
    finalize_asset_upload,
    get_asset_signed_url,
    initiate_asset_upload_session,
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
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]
_PUBLIC_PUBLISHER_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN"})


class CreateUploadSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mime_type: str
    sha256: str
    size_bytes: int
    filename: str | None = None
    asset_role: Literal[
        "RAW_SOURCE",
        "BRANDED_PDF",
        "PREVIEW_PDF",
        "PAGE_RENDER",
        "FIGURE_CROP",
        "AUDIO",
        "THUMBNAIL",
    ] = "RAW_SOURCE"
    requires_pro: bool = False
    visibility: Literal["TENANT_PRIVATE", "SYSTEM_PUBLIC"] = "TENANT_PRIVATE"
    put_ttl_seconds: int = MAX_SIGNED_URL_TTL_SECONDS
    deduplicate: bool = True


class FinalizeAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_sha256: str | None = None
    observed_mime_type: str | None = None
    observed_size_bytes: int | None = None
    scan_status: Literal["PENDING", "CLEAN", "INFECTED"] = "CLEAN"
    page_count: int | None = None
    content_base64: str | None = None


class AssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    parent_asset_id: uuid.UUID | None
    asset_role: str
    object_key: str
    mime_type: str
    sha256: str
    size_bytes: int
    is_immutable_bronze: bool
    finalized: bool
    requires_pro: bool
    visibility: str
    preview_asset_id: uuid.UUID | None
    preview_object_key: str | None
    preview_pages: int | None
    page_count: int | None


class PresignedPutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_scope: str
    object_key: str
    content_type: str
    content_sha256: str
    max_size_bytes: int
    ttl_seconds: int


class UploadSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deduplicated: bool
    session_id: str
    asset: AssetResponse
    put_descriptor: PresignedPutResponse | None = None
    upload_url: str | None = None
    expires_at_epoch: int | None = None


class FinalizeAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: AssetResponse
    preview_asset: AssetResponse | None = None
    session_status: str
    scan_status: str


class SignedUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: uuid.UUID
    variant: str
    object_key: str
    signed_url: str
    ttl_seconds: int
    expires_at_epoch: int
    page_limit: int | None = None
    headers: dict[str, str]


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    request: Request,
    correlation_id: str,
    status_override: int | None = None,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
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


def _serialize_asset(view: AssetView) -> AssetResponse:
    return AssetResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        parent_asset_id=view.parent_asset_id,
        asset_role=view.asset_role,
        object_key=view.object_key,
        mime_type=view.mime_type,
        sha256=view.sha256,
        size_bytes=view.size_bytes,
        is_immutable_bronze=view.is_immutable_bronze,
        finalized=view.finalized,
        requires_pro=view.requires_pro,
        visibility=view.visibility,
        preview_asset_id=view.preview_asset_id,
        preview_object_key=view.preview_object_key,
        preview_pages=view.preview_pages,
        page_count=view.page_count,
    )


@router.post("/upload-sessions")
async def post_upload_session(
    body: CreateUploadSessionRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    visibility = AssetVisibility(body.visibility)
    if visibility == AssetVisibility.SYSTEM_PUBLIC:
        normalized_roles = {r.upper() for r in actor.roles}
        if actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID and not (normalized_roles & _PUBLIC_PUBLISHER_ROLES):
            return _problem_response(
                code=PlatformErrorCode.FORBIDDEN,
                detail="SYSTEM_PUBLIC asset visibility requires system tenant or platform admin role",
                request=request,
                correlation_id=correlation_id,
            )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await initiate_asset_upload_session(
                session,
                tenant_id=actor.tenant_id,
                mime_type=body.mime_type,
                sha256=body.sha256,
                size_bytes=body.size_bytes,
                filename=body.filename,
                asset_role=body.asset_role,
                requires_pro=body.requires_pro,
                visibility=visibility,
                put_ttl_seconds=body.put_ttl_seconds,
                deduplicate=body.deduplicate,
            )
    except (UploadValidationError, SignedUrlPolicyError) as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except IntegrityError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=f"asset constraint violation: {exc.orig}",
            request=request,
            correlation_id=correlation_id,
            status_override=409,
        )

    payload = UploadSessionResponse(
        deduplicated=result.deduplicated,
        session_id=result.session_id,
        asset=_serialize_asset(result.asset),
        put_descriptor=(
            PresignedPutResponse(
                bucket_scope=result.put_descriptor.bucket_scope,
                object_key=result.put_descriptor.object_key,
                content_type=result.put_descriptor.content_type,
                content_sha256=result.put_descriptor.content_sha256,
                max_size_bytes=result.put_descriptor.max_size_bytes,
                ttl_seconds=result.put_descriptor.ttl_seconds,
            )
            if result.put_descriptor is not None
            else None
        ),
        upload_url=result.upload_url,
        expires_at_epoch=result.expires_at_epoch,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED,
        content=jsonable_encoder(payload),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/{asset_id}/finalize")
async def post_finalize_asset(
    asset_id: uuid.UUID,
    body: FinalizeAssetRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    raw_bytes: bytes | None = None
    if body.content_base64 is not None:
        try:
            raw_bytes = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            return _problem_response(
                code=PlatformErrorCode.VALIDATION_ERROR,
                detail=f"invalid content_base64 payload: {exc}",
                request=request,
                correlation_id=correlation_id,
            )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await finalize_asset_upload(
                session,
                tenant_id=actor.tenant_id,
                asset_id=asset_id,
                observed_sha256=body.observed_sha256,
                observed_mime_type=body.observed_mime_type,
                observed_size_bytes=body.observed_size_bytes,
                scan_status=VirusScanStatus(body.scan_status),
                page_count=body.page_count,
                raw_bytes=raw_bytes,
                correlation_id=correlation_id,
            )
    except AssetNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except UploadValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    payload = FinalizeAssetResponse(
        asset=_serialize_asset(result.asset),
        preview_asset=_serialize_asset(result.preview_asset) if result.preview_asset else None,
        session_status=result.session_status,
        scan_status=result.scan_status,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(payload),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/{asset_id}/signed-url")
async def get_signed_asset_url(
    asset_id: uuid.UUID,
    request: Request,
    ttl_seconds: int = Query(default=MAX_SIGNED_URL_TTL_SECONDS),
    inline: bool = Query(default=True),
    credentials: OptionalBearer = None,
) -> JSONResponse:
    actor, correlation_id, err = _authenticate_actor(request, credentials)
    if err is not None:
        return err
    assert actor is not None

    try:
        actor_tier = AssetEntitlementTier(actor.entitlement_tier.upper())
    except ValueError:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=f"unsupported entitlement tier claim: {actor.entitlement_tier!r}",
            request=request,
            correlation_id=correlation_id,
        )

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await get_asset_signed_url(
                session,
                actor_tenant_id=actor.tenant_id,
                asset_id=asset_id,
                actor_tier=actor_tier,
                ttl_seconds=ttl_seconds,
                inline=inline,
            )
    except AssetNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )
    except SignedUrlPolicyError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            request=request,
            correlation_id=correlation_id,
        )

    payload = SignedUrlResponse(
        asset_id=result.asset_id,
        variant=result.variant,
        object_key=result.object_key,
        signed_url=result.signed_url,
        ttl_seconds=result.ttl_seconds,
        expires_at_epoch=result.expires_at_epoch,
        page_limit=result.page_limit,
        headers=result.headers,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(payload),
        headers={"X-Correlation-Id": correlation_id},
    )
