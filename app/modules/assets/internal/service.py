"""Database service layer for B03 assets (upload sessions, finalize, dedup, preview slicing, signed URLs)."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts.events import EventEnvelopeContract
from app.infrastructure.pdf_engine import (
    PDFEngineAdapter,
    PDFEngineError,
    Rect,
    TextStamp,
    slice_pdf,
    stamp_pages,
)
from app.infrastructure.storage import Storage, get_bronze_storage
from app.modules.assets.internal.access import (
    MAX_SIGNED_URL_TTL_SECONDS,
    PREVIEW_MAX_PAGES,
    AssetEntitlementTier,
    AssetNotFoundError,
    AssetRecord,
    AssetVariant,
    AssetVisibility,
    PresignedPutDescriptor,
    SignedUrlPlan,
    SignedUrlPolicyError,
    UploadSession,
    UploadSessionStatus,
    UploadValidationError,
    VirusScanStatus,
    finalize_upload_session,
    initiate_upload_session,
    resolve_asset_download_plan,
)
from app.modules.assets.internal.models import ASSET_ROLES, Asset
from app.modules.outbox.public import enqueue
from app.platform.file_magic import (
    FileSignatureError,
    detect_magic_kind,
    inspect_uploaded_bytes,
)
from app.platform.storage_objects import (
    StorageKeyError,
    build_download_response_headers,
    quarantine_object_key,
    sanitize_download_filename,
)

_MIME_TO_MAGIC_KIND: dict[str, str] = {
    "application/pdf": "PDF",
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}

_EXT_FOR_MIME: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
}


@dataclass(frozen=True, slots=True)
class AssetView:
    """Serialized snapshot of a row in ``assets`` plus its session/preview metadata."""

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
    metadata_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UploadSessionInitResult:
    """Result returned when initiating an asset upload session (or deduplicating)."""

    deduplicated: bool
    session_id: str
    asset: AssetView
    put_descriptor: PresignedPutDescriptor | None
    upload_url: str | None
    expires_at_epoch: int | None


@dataclass(frozen=True, slots=True)
class FinalizeAssetResult:
    """Result returned after finalizing an uploaded asset (and optional preview)."""

    asset: AssetView
    preview_asset: AssetView | None
    session_status: str
    scan_status: str


@dataclass(frozen=True, slots=True)
class SignedDownloadResult:
    """Result returned for a signed GET URL resolution."""

    asset_id: uuid.UUID
    variant: str
    object_key: str
    signed_url: str
    ttl_seconds: int
    expires_at_epoch: int
    page_limit: int | None
    headers: dict[str, str]


def _validate_safe_filename(raw_filename: str | None, *, mime_type: str) -> str:
    if raw_filename is None:
        ext = _EXT_FOR_MIME.get(mime_type, ".bin")
        return f"upload{ext}"
    if not isinstance(raw_filename, str) or not raw_filename.strip():
        raise UploadValidationError("filename must not be empty")
    if "\x00" in raw_filename or "/" in raw_filename or "\\" in raw_filename or ".." in raw_filename:
        raise UploadValidationError("filename contains illegal path traversal sequence")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_filename):
        raise UploadValidationError("filename contains control characters")
    return sanitize_download_filename(raw_filename)


def _validate_safe_object_key(object_key: str) -> str:
    if not isinstance(object_key, str) or not object_key or len(object_key) > 512:
        raise UploadValidationError("object_key must be a non-empty string up to 512 chars")
    if object_key.startswith("/") or "\\" in object_key or "\x00" in object_key:
        raise UploadValidationError("object_key contains illegal characters")
    if any(part in ("", ".", "..") or ".." in part for part in object_key.split("/")):
        raise UploadValidationError("object_key contains path traversal segments")
    return object_key


def _validate_asset_role(asset_role: str) -> str:
    if asset_role not in ASSET_ROLES:
        raise UploadValidationError(f"unsupported asset_role: {asset_role!r}")
    return asset_role


def _sign_local_url(
    path: str,
    *,
    tenant_id: uuid.UUID,
    object_key: str,
    ttl_seconds: int,
    variant: str | None = None,
) -> tuple[str, int]:
    exp = int(time.time()) + ttl_seconds
    secret = get_settings().secret_key.encode()
    msg = f"{path}|{tenant_id}|{object_key}|{variant or ''}|{exp}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]
    variant_q = f"&variant={variant}" if variant else ""
    return f"{path}?tenant_id={tenant_id}&exp={exp}{variant_q}&sig={sig}", exp


def _build_signed_get_url(
    storage: Storage,
    *,
    asset_id: uuid.UUID,
    tenant_id: uuid.UUID,
    plan: SignedUrlPlan,
    filename: str,
) -> tuple[str, int]:
    presigned = storage.presigned_url(
        plan.object_key,
        expires_seconds=plan.ttl_seconds,
        download_filename=filename,
    )
    if presigned:
        return presigned, int(time.time()) + plan.ttl_seconds
    return _sign_local_url(
        f"/api/v1/assets/{asset_id}/download",
        tenant_id=tenant_id,
        object_key=plan.object_key,
        ttl_seconds=plan.ttl_seconds,
        variant=plan.variant.value,
    )


def _slice_pdf_preview_bytes(pdf_bytes: bytes, max_pages: int = PREVIEW_MAX_PAGES) -> tuple[bytes, int]:
    """Verify PDF readability with PDFEngineAdapter and render a max-2-page preview slice."""
    try:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            total_pages = pdf.page_count
    except PDFEngineError as exc:
        raise UploadValidationError(f"invalid PDF payload: {exc}") from exc

    if total_pages <= 0:
        raise UploadValidationError("PDF must contain at least 1 page")

    take = min(max(1, max_pages), total_pages)
    sliced = slice_pdf(pdf_bytes, 0, take - 1)

    def _banner(index: int, width: float, height: float) -> list[TextStamp]:
        banner = (
            f"PREVIEW ({index + 1}/{take} of {total_pages} pages) · "
            "Upgrade to PRO to unlock full document"
        )
        footer_rect = Rect(16, max(4, height - 22), max(40, width - 16), height - 4)
        return [
            TextStamp(
                banner,
                footer_rect,
                8.5,
                (0.45, 0.18, 0.05),
                "center",
                background=(0.95, 0.96, 0.98),
            )
        ]

    return stamp_pages(sliced, _banner), total_pages


def _verify_bytes_against_mime(*, raw_bytes: bytes, filename: str, expected_mime: str) -> None:
    expected_kind = _MIME_TO_MAGIC_KIND.get(expected_mime)
    if expected_kind is not None:
        ext = _EXT_FOR_MIME[expected_mime]
        check_name = filename if filename.lower().endswith(ext) else f"upload{ext}"
        try:
            inspect_uploaded_bytes(filename=check_name, raw_bytes=raw_bytes)
        except FileSignatureError as exc:
            raise UploadValidationError(f"magic-byte verification failed: {exc}") from exc
        return

    if expected_mime == "audio/mpeg":
        detected = detect_magic_kind(raw_bytes)
        if detected != "UNKNOWN":
            raise UploadValidationError(f"expected audio/mpeg but detected {detected}")
        is_id3 = raw_bytes.startswith(b"ID3")
        is_mp3_frame = len(raw_bytes) >= 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0
        if not (is_id3 or is_mp3_frame):
            raise UploadValidationError("audio/mpeg payload missing valid ID3 or MPEG frame sync header")
        return

    raise UploadValidationError(f"unsupported mime type: {expected_mime!r}")


def _to_view(row: Asset) -> AssetView:
    meta = dict(row.metadata_json or {})
    session_meta = meta.get("upload_session") or {}
    finalized = bool(meta.get("finalized") or session_meta.get("status") == UploadSessionStatus.FINALIZED.value)
    preview_asset_raw = meta.get("preview_asset_id")
    preview_asset_id = uuid.UUID(str(preview_asset_raw)) if preview_asset_raw else None
    preview_pages_raw = meta.get("preview_pages")
    page_count_raw = meta.get("page_count")
    return AssetView(
        id=row.id,
        tenant_id=row.tenant_id,
        parent_asset_id=row.parent_asset_id,
        asset_role=row.asset_role,
        object_key=row.object_key,
        mime_type=row.mime_type,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        is_immutable_bronze=bool(row.is_immutable_bronze),
        finalized=finalized,
        requires_pro=bool(meta.get("requires_pro", False)),
        visibility=str(meta.get("visibility", AssetVisibility.TENANT_PRIVATE.value)),
        preview_asset_id=preview_asset_id,
        preview_object_key=meta.get("preview_object_key"),
        preview_pages=int(preview_pages_raw) if preview_pages_raw is not None else None,
        page_count=int(page_count_raw) if page_count_raw is not None else None,
        metadata_json=meta,
    )


def _session_from_asset(row: Asset) -> UploadSession:
    meta = row.metadata_json or {}
    raw = meta.get("upload_session")
    if not isinstance(raw, dict):
        raise UploadValidationError(f"asset {row.id} has no upload session metadata")
    return UploadSession(
        session_id=str(raw["session_id"]),
        tenant_id=str(raw["tenant_id"]),
        asset_id=str(raw["asset_id"]),
        declared_mime_type=str(raw["declared_mime_type"]),
        declared_sha256=str(raw["declared_sha256"]),
        declared_size_bytes=int(raw["declared_size_bytes"]),
        object_key=str(raw["object_key"]),
        requires_pro=bool(raw.get("requires_pro", False)),
        visibility=AssetVisibility(str(raw.get("visibility", AssetVisibility.TENANT_PRIVATE.value))),
        status=UploadSessionStatus(str(raw.get("status", UploadSessionStatus.INITIATED.value))),
        rejection_reason=raw.get("rejection_reason"),
    )


def _record_from_asset(row: Asset, *, preview_key_override: str | None = None) -> AssetRecord:
    meta = row.metadata_json or {}
    session_meta = meta.get("upload_session") or {}
    finalized = bool(meta.get("finalized") or session_meta.get("status") == UploadSessionStatus.FINALIZED.value)
    visibility_str = str(meta.get("visibility", AssetVisibility.TENANT_PRIVATE.value))
    preview_key = preview_key_override if preview_key_override is not None else meta.get("preview_object_key")
    preview_pages = meta.get("preview_pages")
    return AssetRecord(
        asset_id=str(row.id),
        tenant_id=str(row.tenant_id),
        mime_type=row.mime_type,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        original_object_key=row.object_key,
        preview_object_key=preview_key,
        preview_pages=int(preview_pages) if preview_pages is not None else None,
        requires_pro=bool(meta.get("requires_pro", False)),
        visibility=AssetVisibility(visibility_str),
        finalized=finalized,
    )


async def initiate_asset_upload_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    mime_type: str,
    sha256: str,
    size_bytes: int,
    filename: str | None = None,
    asset_role: str = "RAW_SOURCE",
    requires_pro: bool = False,
    visibility: AssetVisibility = AssetVisibility.TENANT_PRIVATE,
    put_ttl_seconds: int = MAX_SIGNED_URL_TTL_SECONDS,
    custom_object_key: str | None = None,
    deduplicate: bool = True,
) -> UploadSessionInitResult:
    """Initiate a tenant-scoped upload session or deduplicate against a finalized asset."""
    _validate_asset_role(asset_role)
    safe_mime = mime_type.strip().lower() if isinstance(mime_type, str) else ""
    safe_filename = _validate_safe_filename(filename, mime_type=safe_mime)
    if custom_object_key is not None:
        _validate_safe_object_key(custom_object_key)

    asset_id = uuid.uuid4()
    upload_session_id = f"up_{uuid.uuid4().hex}"
    pure_session, put_desc = initiate_upload_session(
        session_id=upload_session_id,
        tenant_id=str(tenant_id),
        asset_id=str(asset_id),
        declared_mime_type=mime_type,
        declared_sha256=sha256,
        declared_size_bytes=size_bytes,
        requires_pro=requires_pro,
        visibility=visibility,
        put_ttl_seconds=put_ttl_seconds,
    )

    if deduplicate:
        stmt = (
            select(Asset)
            .where(
                Asset.tenant_id == tenant_id,
                Asset.sha256 == pure_session.declared_sha256,
                Asset.asset_role == asset_role,
                Asset.parent_asset_id.is_(None),
            )
            .order_by(Asset.created_at.desc())
        )
        candidates = (await session.execute(stmt)).scalars().all()
        for existing in candidates:
            existing_meta = existing.metadata_json or {}
            existing_sess = existing_meta.get("upload_session") or {}
            is_finalized = bool(
                existing_meta.get("finalized") or existing_sess.get("status") == UploadSessionStatus.FINALIZED.value
            )
            if is_finalized and existing.mime_type == pure_session.declared_mime_type:
                return UploadSessionInitResult(
                    deduplicated=True,
                    session_id=str(existing_sess.get("session_id") or f"dedup_{existing.id.hex[:12]}"),
                    asset=_to_view(existing),
                    put_descriptor=None,
                    upload_url=None,
                    expires_at_epoch=None,
                )

    expires_at_epoch = int(time.time()) + put_desc.ttl_seconds
    upload_url, _ = _sign_local_url(
        f"/api/v1/assets/{asset_id}/upload",
        tenant_id=tenant_id,
        object_key=pure_session.object_key,
        ttl_seconds=put_desc.ttl_seconds,
    )

    metadata: dict[str, Any] = {
        "filename": safe_filename,
        "requires_pro": requires_pro,
        "visibility": visibility.value,
        "finalized": False,
        "scan_status": VirusScanStatus.PENDING.value,
        "upload_session": {
            "session_id": pure_session.session_id,
            "tenant_id": pure_session.tenant_id,
            "asset_id": pure_session.asset_id,
            "declared_mime_type": pure_session.declared_mime_type,
            "declared_sha256": pure_session.declared_sha256,
            "declared_size_bytes": pure_session.declared_size_bytes,
            "object_key": pure_session.object_key,
            "requires_pro": pure_session.requires_pro,
            "visibility": pure_session.visibility.value,
            "status": pure_session.status.value,
            "expires_at_epoch": expires_at_epoch,
            "rejection_reason": None,
        },
    }
    row = Asset(
        id=asset_id,
        tenant_id=tenant_id,
        parent_asset_id=None,
        asset_role=asset_role,
        object_key=pure_session.object_key,
        mime_type=pure_session.declared_mime_type,
        sha256=pure_session.declared_sha256,
        size_bytes=pure_session.declared_size_bytes,
        metadata_json=metadata,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    return UploadSessionInitResult(
        deduplicated=False,
        session_id=pure_session.session_id,
        asset=_to_view(row),
        put_descriptor=put_desc,
        upload_url=upload_url,
        expires_at_epoch=expires_at_epoch,
    )


async def _create_or_update_preview_asset(
    session: AsyncSession,
    *,
    parent_row: Asset,
    preview_key: str,
    preview_bytes: bytes | None,
    fallback_sha256: str,
    preview_pages: int,
    total_pages: int,
    visibility: AssetVisibility,
    requires_pro: bool,
    storage: Storage,
) -> Asset:
    if preview_bytes is not None:
        await storage.put(preview_key, preview_bytes, "application/pdf")
        preview_sha256 = hashlib.sha256(preview_bytes).hexdigest()
        preview_size = len(preview_bytes)
    else:
        preview_sha256 = fallback_sha256
        preview_size = parent_row.size_bytes

    existing = (
        await session.execute(select(Asset).where(Asset.object_key == preview_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    preview_row = Asset(
        id=uuid.uuid4(),
        tenant_id=parent_row.tenant_id,
        parent_asset_id=parent_row.id,
        asset_role="PREVIEW_PDF",
        object_key=preview_key,
        mime_type="application/pdf",
        sha256=preview_sha256,
        size_bytes=preview_size,
        metadata_json={
            "filename": f"preview_p{preview_pages}.pdf",
            "requires_pro": False,
            "parent_requires_pro": requires_pro,
            "visibility": visibility.value,
            "finalized": True,
            "preview_pages": preview_pages,
            "page_count": total_pages,
            "scan_status": VirusScanStatus.CLEAN.value,
        },
    )
    session.add(preview_row)
    await session.flush()
    await session.refresh(preview_row)
    return preview_row


async def finalize_asset_upload(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    observed_sha256: str | None = None,
    observed_mime_type: str | None = None,
    observed_size_bytes: int | None = None,
    scan_status: VirusScanStatus = VirusScanStatus.CLEAN,
    page_count: int | None = None,
    raw_bytes: bytes | None = None,
    correlation_id: str = "assets-finalize",
    storage: Storage | None = None,
) -> FinalizeAssetResult:
    """Verify checksum/magic bytes/scan status, slice 2-page PDF preview if PDF, and finalize asset."""
    row = (await session.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if row is None or row.tenant_id != tenant_id:
        raise AssetNotFoundError(f"asset {asset_id} not found")

    pure_session = _session_from_asset(row)
    meta = dict(row.metadata_json or {})
    session_meta = dict(meta.get("upload_session") or {})
    expires_at_epoch = session_meta.get("expires_at_epoch")
    if (
        pure_session.status != UploadSessionStatus.FINALIZED
        and expires_at_epoch is not None
        and int(time.time()) > int(expires_at_epoch)
    ):
        raise UploadValidationError("upload session has expired")

    active_storage = storage or get_bronze_storage()
    payload_bytes = raw_bytes
    if payload_bytes is None and await active_storage.exists(row.object_key):
        payload_bytes = await active_storage.get(row.object_key)
    elif payload_bytes is not None:
        await active_storage.put(row.object_key, payload_bytes, pure_session.declared_mime_type)

    preview_bytes: bytes | None = None
    effective_page_count = page_count

    if payload_bytes is not None:
        computed_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        computed_size = len(payload_bytes)
        effective_sha256 = observed_sha256 or computed_sha256
        effective_size = observed_size_bytes if observed_size_bytes is not None else computed_size
        effective_mime = observed_mime_type or pure_session.declared_mime_type

        if computed_sha256 != pure_session.declared_sha256:
            raise UploadValidationError("uploaded payload sha256 does not match declared sha256")
        if computed_size != pure_session.declared_size_bytes:
            raise UploadValidationError("uploaded payload size does not match declared size_bytes")

        _verify_bytes_against_mime(
            raw_bytes=payload_bytes,
            filename=str(meta.get("filename") or "upload.bin"),
            expected_mime=pure_session.declared_mime_type,
        )
        if pure_session.declared_mime_type == "application/pdf":
            preview_bytes, detected_pages = _slice_pdf_preview_bytes(payload_bytes, max_pages=PREVIEW_MAX_PAGES)
            effective_page_count = detected_pages
    else:
        effective_sha256 = observed_sha256 or row.sha256
        effective_mime = observed_mime_type or row.mime_type
        effective_size = observed_size_bytes if observed_size_bytes is not None else row.size_bytes

    if scan_status == VirusScanStatus.INFECTED:
        quarantine_key = quarantine_object_key(
            tenant_id=tenant_id,
            job_id=pure_session.session_id,
            sha256=pure_session.declared_sha256,
        )
        if payload_bytes is not None:
            await active_storage.put(quarantine_key, payload_bytes, "application/octet-stream")
            await active_storage.delete(row.object_key)
        session_meta["status"] = UploadSessionStatus.REJECTED.value
        session_meta["rejection_reason"] = "virus_scan_infected"
        meta["upload_session"] = session_meta
        meta["scan_status"] = VirusScanStatus.INFECTED.value
        meta["quarantine_object_key"] = quarantine_key
        meta["finalized"] = False
        row.metadata_json = meta
        await session.flush()

    finalized_session, record = finalize_upload_session(
        pure_session,
        observed_sha256=effective_sha256,
        observed_mime_type=effective_mime,
        observed_size_bytes=effective_size,
        scan_status=scan_status,
        page_count=effective_page_count,
    )

    preview_row: Asset | None = None
    if record.preview_object_key and record.preview_pages:
        preview_row = await _create_or_update_preview_asset(
            session,
            parent_row=row,
            preview_key=record.preview_object_key,
            preview_bytes=preview_bytes,
            fallback_sha256=record.sha256,
            preview_pages=record.preview_pages,
            total_pages=effective_page_count or record.preview_pages,
            visibility=record.visibility,
            requires_pro=record.requires_pro,
            storage=active_storage,
        )

    session_meta["status"] = finalized_session.status.value
    session_meta["rejection_reason"] = None
    meta["upload_session"] = session_meta
    meta["scan_status"] = scan_status.value
    meta["finalized"] = True
    meta["preview_object_key"] = record.preview_object_key
    meta["preview_pages"] = record.preview_pages
    meta["page_count"] = effective_page_count
    if preview_row is not None:
        meta["preview_asset_id"] = str(preview_row.id)
    row.metadata_json = meta
    await session.flush()
    await session.refresh(row)

    await enqueue(
        session,
        EventEnvelopeContract(
            event_type="asset.finalized",
            tenant_id=tenant_id,
            aggregate_type="asset",
            aggregate_id=row.id,
            correlation_id=correlation_id,
            payload={
                "asset_id": str(row.id),
                "asset_role": row.asset_role,
                "mime_type": row.mime_type,
                "sha256": row.sha256,
                "size_bytes": row.size_bytes,
                "preview_asset_id": str(preview_row.id) if preview_row else None,
            },
        ),
    )

    return FinalizeAssetResult(
        asset=_to_view(row),
        preview_asset=_to_view(preview_row) if preview_row else None,
        session_status=finalized_session.status.value,
        scan_status=scan_status.value,
    )


async def get_asset_signed_url(
    session: AsyncSession,
    *,
    actor_tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    actor_tier: AssetEntitlementTier = AssetEntitlementTier.FREE,
    ttl_seconds: int = MAX_SIGNED_URL_TTL_SECONDS,
    inline: bool = True,
    storage: Storage | None = None,
) -> SignedDownloadResult:
    """Resolve signed GET URL plan enforcing hybrid RLS, 404 IDOR guard, <=900s TTL, and FREE preview cap."""
    row = (await session.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if row is None:
        raise AssetNotFoundError(f"asset {asset_id} not found")

    meta = dict(row.metadata_json or {})
    preview_key_override: str | None = meta.get("preview_object_key")
    if preview_key_override is None:
        child_preview = (
            await session.execute(
                select(Asset).where(
                    Asset.parent_asset_id == row.id,
                    Asset.asset_role == "PREVIEW_PDF",
                )
            )
        ).scalar_one_or_none()
        if child_preview is not None:
            preview_key_override = child_preview.object_key

    record = _record_from_asset(row, preview_key_override=preview_key_override)
    plan = resolve_asset_download_plan(
        record,
        actor_tenant_id=str(actor_tenant_id),
        actor_tier=actor_tier,
        ttl_seconds=ttl_seconds,
    )

    active_storage = storage or get_bronze_storage()
    filename = str(meta.get("filename") or f"{row.id}{_EXT_FOR_MIME.get(row.mime_type, '.bin')}")
    try:
        headers = build_download_response_headers(
            filename=filename,
            mime_type=row.mime_type,
            inline=inline,
            public_immutable=(
                record.visibility == AssetVisibility.SYSTEM_PUBLIC
                and plan.variant != AssetVariant.ORIGINAL_PDF
                and not record.requires_pro
            ),
        )
    except StorageKeyError as exc:
        raise SignedUrlPolicyError(str(exc)) from exc

    signed_url, expires_at = _build_signed_get_url(
        active_storage,
        asset_id=row.id,
        tenant_id=actor_tenant_id,
        plan=plan,
        filename=filename,
    )
    return SignedDownloadResult(
        asset_id=row.id,
        variant=plan.variant.value,
        object_key=plan.object_key,
        signed_url=signed_url,
        ttl_seconds=plan.ttl_seconds,
        expires_at_epoch=expires_at,
        page_limit=plan.page_limit,
        headers=headers,
    )


async def get_asset_by_id(session: AsyncSession, *, asset_id: uuid.UUID) -> AssetView:
    """Fetch one asset row visible under current tenant RLS context."""
    row = (await session.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if row is None:
        raise AssetNotFoundError(f"asset {asset_id} not found")
    return _to_view(row)


async def register_bronze_asset_from_bytes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_bytes: bytes,
    filename: str,
    mime_type: str,
    requires_pro: bool = False,
    visibility: AssetVisibility = AssetVisibility.TENANT_PRIVATE,
    correlation_id: str = "ingestion-bronze-register",
    storage: Storage | None = None,
) -> tuple[AssetView, bool]:
    """Store, verify, deduplicate, and finalize a RAW_SOURCE asset directly from verified bytes."""
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    init_res = await initiate_asset_upload_session(
        session,
        tenant_id=tenant_id,
        mime_type=mime_type,
        sha256=sha256,
        size_bytes=len(raw_bytes),
        filename=filename,
        asset_role="RAW_SOURCE",
        requires_pro=requires_pro,
        visibility=visibility,
        deduplicate=True,
    )
    if init_res.deduplicated:
        return init_res.asset, True

    fin_res = await finalize_asset_upload(
        session,
        tenant_id=tenant_id,
        asset_id=init_res.asset.id,
        observed_sha256=sha256,
        observed_mime_type=mime_type,
        observed_size_bytes=len(raw_bytes),
        scan_status=VirusScanStatus.CLEAN,
        raw_bytes=raw_bytes,
        correlation_id=correlation_id,
        storage=storage,
    )
    return fin_res.asset, False
