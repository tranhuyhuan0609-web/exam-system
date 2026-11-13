"""Canonical MinIO/S3 object key layout, path-traversal shield, and safe download headers."""

from __future__ import annotations

import re
import uuid

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ALLOWED_FIGURE_EXTS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "webp"})


class StorageKeyError(ValueError):
    """Raised when an object storage segment, digest, or tenant ownership check fails."""


def _validate_sha256(digest: str) -> str:
    cleaned = digest.strip().lower()
    if not _SHA256_RE.match(cleaned):
        raise StorageKeyError("sha256 must be a 64-character lowercase hex digest")
    return cleaned


def _validate_segment(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned or ".." in cleaned or not _SAFE_SEGMENT_RE.match(cleaned):
        raise StorageKeyError(f"unsafe or invalid {label} segment: {value!r}")
    return cleaned


def bronze_object_key(*, tenant_id: uuid.UUID, sha256: str) -> str:
    """Build immutable bronze-layer key partitioned by tenant_id and SHA-256 fanout prefix."""
    digest = _validate_sha256(sha256)
    return f"bronze/{tenant_id}/{digest[:2]}/{digest}"


def silver_figure_key(
    *,
    tenant_id: uuid.UUID,
    document_version_id: uuid.UUID,
    page_no: int,
    region_id: str,
    ext: str = "png",
) -> str:
    """Build silver-layer cropped figure/table image key."""
    if page_no <= 0 or page_no > 9999:
        raise StorageKeyError(f"page_no out of bounds (1..9999): {page_no}")
    clean_ext = ext.strip().lower().lstrip(".")
    if clean_ext not in _ALLOWED_FIGURE_EXTS:
        raise StorageKeyError(f"unsupported figure extension: {ext!r}")
    safe_region = _validate_segment(region_id, "region_id")
    return f"silver/{tenant_id}/{document_version_id}/p{page_no:04d}_{safe_region}.{clean_ext}"


def preview_pdf_key(
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    sha256: str,
    preview_pages: int = 2,
) -> str:
    """Build key for limited-page PREVIEW_PDF slices shown to FREE-tier learners."""
    if preview_pages <= 0 or preview_pages > 10:
        raise StorageKeyError(f"preview_pages out of bounds (1..10): {preview_pages}")
    digest = _validate_sha256(sha256)
    return f"previews/{tenant_id}/{asset_id}/{digest}_p{preview_pages}.pdf"


def quarantine_object_key(*, tenant_id: uuid.UUID, job_id: str, sha256: str) -> str:
    """Build isolated key for infected or rejected uploads held out of active layers."""
    safe_job = _validate_segment(job_id, "job_id")
    digest = _validate_sha256(sha256)
    return f"quarantine/{tenant_id}/{safe_job}/{digest}"


def verify_object_key_tenant_ownership(*, object_key: str, expected_tenant_id: uuid.UUID) -> None:
    """Ensure every path segment is normalized and scoped to expected_tenant_id."""
    if not object_key or "\x00" in object_key or "\\" in object_key:
        raise StorageKeyError("object_key contains illegal characters")
    parts = object_key.split("/")
    if len(parts) < 3 or any(p in {"", ".", ".."} for p in parts):
        raise StorageKeyError(f"malformed or traversal object_key: {object_key!r}")
    layer, tenant_part = parts[0], parts[1]
    if layer not in {"bronze", "silver", "previews", "quarantine"}:
        raise StorageKeyError(f"unknown storage layer {layer!r}")
    try:
        key_tenant = uuid.UUID(tenant_part)
    except ValueError as exc:
        raise StorageKeyError("object_key missing valid tenant UUID segment") from exc
    if key_tenant != expected_tenant_id:
        raise StorageKeyError("object_key tenant segment does not match expected_tenant_id")


def sanitize_download_filename(raw_name: str, *, fallback: str = "document.bin") -> str:
    """Strip path separators, quotes, and control chars for RFC 6266 Content-Disposition."""
    base = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r'[^A-Za-z0-9._ -]', "_", base).strip(" .")
    if not cleaned or ".." in cleaned:
        return fallback
    return cleaned[:120]


def build_download_response_headers(
    *,
    filename: str,
    mime_type: str,
    inline: bool = False,
    public_immutable: bool = False,
) -> dict[str, str]:
    """Produce hardened HTTP headers preventing MIME sniffing and header injection on asset downloads."""
    safe_name = sanitize_download_filename(filename)
    disposition = "inline" if inline else "attachment"
    cache_control = "public, max-age=31536000, immutable" if public_immutable else "private, no-store"
    return {
        "Content-Type": mime_type.strip().lower(),
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": cache_control,
    }
