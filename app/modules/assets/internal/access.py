"""B03 Media & Asset Management core rules.

Two-step upload finalize, ClamAV/MIME/SHA256, and signed URL access policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

MAX_SIGNED_URL_TTL_SECONDS = 900
PREVIEW_MAX_PAGES = 2
MAX_ASSET_BYTES = 100 * 1024 * 1024

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "audio/mpeg",
    }
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Identifiers become part of object keys: no separators, dots or whitespace, so no traversal.
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class UploadSessionStatus(StrEnum):
    INITIATED = "INITIATED"
    UPLOADED = "UPLOADED"
    FINALIZED = "FINALIZED"
    REJECTED = "REJECTED"


class VirusScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"


class AssetVariant(StrEnum):
    ORIGINAL_PDF = "ORIGINAL_PDF"
    PREVIEW_PDF = "PREVIEW_PDF"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"


class AssetEntitlementTier(StrEnum):
    FREE = "FREE"
    STUDENT_PRO = "STUDENT_PRO"
    TEACHER_PRO = "TEACHER_PRO"
    SCHOOL_ENTERPRISE = "SCHOOL_ENTERPRISE"


class AssetVisibility(StrEnum):
    TENANT_PRIVATE = "TENANT_PRIVATE"
    SYSTEM_PUBLIC = "SYSTEM_PUBLIC"


class AssetNotFoundError(LookupError):
    """Raised when an asset is missing or inaccessible across tenant boundaries (HTTP 404)."""


class UploadValidationError(ValueError):
    """Raised when an upload session fails SHA-256, MIME, size, or antivirus checks."""


class SignedUrlPolicyError(ValueError):
    """Raised when signed URL issuance violates TTL, readiness, or variant rules."""


@dataclass(frozen=True)
class UploadSession:
    session_id: str
    tenant_id: str
    asset_id: str
    declared_mime_type: str
    declared_sha256: str
    declared_size_bytes: int
    object_key: str
    requires_pro: bool = False
    visibility: AssetVisibility = AssetVisibility.TENANT_PRIVATE
    status: UploadSessionStatus = UploadSessionStatus.INITIATED
    rejection_reason: str | None = None


@dataclass(frozen=True)
class PresignedPutDescriptor:
    bucket_scope: str
    object_key: str
    content_type: str
    content_sha256: str
    max_size_bytes: int
    ttl_seconds: int


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    tenant_id: str
    mime_type: str
    sha256: str
    size_bytes: int
    original_object_key: str
    preview_object_key: str | None
    preview_pages: int | None
    requires_pro: bool
    visibility: AssetVisibility
    finalized: bool = True


@dataclass(frozen=True)
class SignedUrlPlan:
    asset_id: str
    variant: AssetVariant
    object_key: str
    ttl_seconds: int
    page_limit: int | None


def _safe_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise UploadValidationError(f"{name} must match [A-Za-z0-9_-]{{1,64}}")
    return value


def _normalize_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise UploadValidationError("sha256 must be a string")
    cleaned = value.strip().lower()
    if not _SHA256_RE.fullmatch(cleaned):
        raise UploadValidationError("sha256 must be a 64-character lowercase hex digest")
    return cleaned


def _normalize_mime(value: str) -> str:
    if not isinstance(value, str):
        raise UploadValidationError("mime type must be a string")
    cleaned = value.strip().lower().split(";", 1)[0].strip()
    if cleaned not in ALLOWED_MIME_TYPES:
        raise UploadValidationError(f"unsupported mime type: {value!r}")
    return cleaned


def _variant_for_mime(mime_type: str) -> AssetVariant:
    if mime_type == "application/pdf":
        return AssetVariant.ORIGINAL_PDF
    if mime_type.startswith("image/"):
        return AssetVariant.IMAGE
    return AssetVariant.AUDIO


def initiate_upload_session(
    *,
    session_id: str,
    tenant_id: str,
    asset_id: str,
    declared_mime_type: str,
    declared_sha256: str,
    declared_size_bytes: int,
    requires_pro: bool = False,
    visibility: AssetVisibility = AssetVisibility.TENANT_PRIVATE,
    put_ttl_seconds: int = MAX_SIGNED_URL_TTL_SECONDS,
) -> tuple[UploadSession, PresignedPutDescriptor]:
    """Create step-1 private upload session and constrained Presigned PUT descriptor."""
    session_id = _safe_id("session_id", session_id)
    tenant_id = _safe_id("tenant_id", tenant_id)
    asset_id = _safe_id("asset_id", asset_id)
    if visibility not in tuple(AssetVisibility):
        raise UploadValidationError(f"unknown visibility: {visibility!r}")
    if isinstance(declared_size_bytes, bool) or not isinstance(declared_size_bytes, int):
        raise UploadValidationError("declared_size_bytes must be an integer")
    if declared_size_bytes <= 0 or declared_size_bytes > MAX_ASSET_BYTES:
        raise UploadValidationError(f"declared_size_bytes out of bounds: {declared_size_bytes}")
    if isinstance(put_ttl_seconds, bool) or not isinstance(put_ttl_seconds, int):
        raise SignedUrlPolicyError("PUT TTL must be an integer number of seconds")
    if put_ttl_seconds <= 0 or put_ttl_seconds > MAX_SIGNED_URL_TTL_SECONDS:
        raise SignedUrlPolicyError(f"PUT TTL must be between 1 and {MAX_SIGNED_URL_TTL_SECONDS}s")

    norm_mime = _normalize_mime(declared_mime_type)
    norm_sha = _normalize_sha256(declared_sha256)
    object_key = f"private/{tenant_id}/assets/{asset_id}/{norm_sha}"

    session = UploadSession(
        session_id=session_id,
        tenant_id=tenant_id,
        asset_id=asset_id,
        declared_mime_type=norm_mime,
        declared_sha256=norm_sha,
        declared_size_bytes=declared_size_bytes,
        object_key=object_key,
        requires_pro=requires_pro,
        visibility=visibility,
    )
    put_desc = PresignedPutDescriptor(
        bucket_scope="private",
        object_key=object_key,
        content_type=norm_mime,
        content_sha256=norm_sha,
        max_size_bytes=declared_size_bytes,
        ttl_seconds=put_ttl_seconds,
    )
    return session, put_desc


def finalize_upload_session(
    session: UploadSession,
    *,
    observed_sha256: str,
    observed_mime_type: str,
    observed_size_bytes: int,
    scan_status: VirusScanStatus,
    page_count: int | None = None,
) -> tuple[UploadSession, AssetRecord]:
    """Step-2 finalize: verify checksum, real MIME, ClamAV CLEAN result, and PDF preview metadata."""
    if session.status not in (UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADED):
        raise UploadValidationError(f"session cannot finalize from status {session.status}")

    norm_sha = _normalize_sha256(observed_sha256)
    if norm_sha != session.declared_sha256:
        raise UploadValidationError("observed sha256 does not match declared sha256")

    norm_mime = _normalize_mime(observed_mime_type)
    if norm_mime != session.declared_mime_type:
        raise UploadValidationError(f"observed mime {norm_mime} mismatches declared {session.declared_mime_type}")

    if isinstance(observed_size_bytes, bool) or not isinstance(observed_size_bytes, int):
        raise UploadValidationError("observed size must be an integer")
    if observed_size_bytes <= 0 or observed_size_bytes != session.declared_size_bytes:
        raise UploadValidationError("observed file size does not match declared size")

    if scan_status == VirusScanStatus.INFECTED:
        raise UploadValidationError("virus scan detected malware (INFECTED)")
    if scan_status != VirusScanStatus.CLEAN:
        raise UploadValidationError(f"virus scan not clean: {scan_status}")

    preview_key: str | None = None
    preview_pages: int | None = None
    if norm_mime == "application/pdf":
        if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count <= 0:
            raise UploadValidationError("PDF assets require positive page_count for preview generation")
        preview_pages = min(page_count, PREVIEW_MAX_PAGES)
        preview_key = f"private/{session.tenant_id}/previews/{session.asset_id}/{norm_sha}_p{preview_pages}.pdf"

    finalized_session = replace(session, status=UploadSessionStatus.FINALIZED, rejection_reason=None)
    record = AssetRecord(
        asset_id=session.asset_id,
        tenant_id=session.tenant_id,
        mime_type=norm_mime,
        sha256=norm_sha,
        size_bytes=observed_size_bytes,
        original_object_key=session.object_key,
        preview_object_key=preview_key,
        preview_pages=preview_pages,
        requires_pro=session.requires_pro,
        visibility=session.visibility,
        finalized=True,
    )
    return finalized_session, record


def resolve_asset_download_plan(
    asset: AssetRecord,
    *,
    actor_tenant_id: str,
    actor_tier: AssetEntitlementTier,
    ttl_seconds: int = MAX_SIGNED_URL_TTL_SECONDS,
) -> SignedUrlPlan:
    """Resolve signed GET URL plan enforcing tenant RLS (404 IDOR guard), <=15m TTL, and FREE preview-only cap."""
    if not isinstance(actor_tenant_id, str) or not actor_tenant_id.strip():
        raise AssetNotFoundError(f"asset {asset.asset_id!r} not found")
    if actor_tier not in tuple(AssetEntitlementTier):
        raise SignedUrlPolicyError(f"unknown entitlement tier: {actor_tier!r}")
    if asset.visibility != AssetVisibility.SYSTEM_PUBLIC and (
        asset.visibility != AssetVisibility.TENANT_PRIVATE or asset.tenant_id != actor_tenant_id
    ):
        # Unknown visibility values are treated as private (fail closed).
        raise AssetNotFoundError(f"asset {asset.asset_id!r} not found")
    if not asset.finalized:
        raise SignedUrlPolicyError("cannot issue signed URL before upload finalize")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise SignedUrlPolicyError("GET TTL must be an integer number of seconds")
    if ttl_seconds <= 0 or ttl_seconds > MAX_SIGNED_URL_TTL_SECONDS:
        raise SignedUrlPolicyError(f"GET TTL must be within 1..{MAX_SIGNED_URL_TTL_SECONDS}s")

    if asset.mime_type == "application/pdf":
        is_paid_tier = actor_tier != AssetEntitlementTier.FREE
        if asset.requires_pro and not is_paid_tier:
            if not asset.preview_object_key:
                raise SignedUrlPolicyError("PRO PDF missing generated PREVIEW_PDF artifact")
            return SignedUrlPlan(
                asset_id=asset.asset_id,
                variant=AssetVariant.PREVIEW_PDF,
                object_key=asset.preview_object_key,
                ttl_seconds=ttl_seconds,
                page_limit=asset.preview_pages or PREVIEW_MAX_PAGES,
            )
        return SignedUrlPlan(
            asset_id=asset.asset_id,
            variant=AssetVariant.ORIGINAL_PDF,
            object_key=asset.original_object_key,
            ttl_seconds=ttl_seconds,
            page_limit=None,
        )

    if asset.requires_pro and actor_tier == AssetEntitlementTier.FREE:
        raise SignedUrlPolicyError("PRO non-PDF asset requires paid entitlement tier")

    return SignedUrlPlan(
        asset_id=asset.asset_id,
        variant=_variant_for_mime(asset.mime_type),
        object_key=asset.original_object_key,
        ttl_seconds=ttl_seconds,
        page_limit=None,
    )
