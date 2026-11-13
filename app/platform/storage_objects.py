"""Canonical MinIO/S3 object key layout, path-traversal shield, and safe download headers."""

from __future__ import annotations

import re
import uuid

_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SAFE_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]{1,128}", re.ASCII)
_TOKEN = r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
_MIME_RE = re.compile(rf"{_TOKEN}/{_TOKEN}", re.ASCII)
_ALLOWED_FIGURE_EXTS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "webp"})
_STORAGE_LAYERS: frozenset[str] = frozenset({"bronze", "silver", "previews", "quarantine"})
_INLINE_CSP = "default-src 'none'; sandbox"


class StorageKeyError(ValueError):
    """Raised when an object storage segment, digest, header value or tenant ownership check fails."""


def _require_uuid(value: object, label: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise StorageKeyError(f"{label} must be a UUID")
    return value


def _validate_sha256(digest: object) -> str:
    if not isinstance(digest, str):
        raise StorageKeyError("sha256 must be a 64-character lowercase hex digest")
    cleaned = digest.strip().lower()
    if _SHA256_RE.fullmatch(cleaned) is None:
        raise StorageKeyError("sha256 must be a 64-character lowercase hex digest")
    return cleaned


def _is_safe_segment(value: str) -> bool:
    # A segment made only of dots ("." / "..") is a path-navigation token, never a name.
    return (
        _SAFE_SEGMENT_RE.fullmatch(value) is not None
        and ".." not in value
        and value.strip(".") != ""
    )


def _validate_segment(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise StorageKeyError(f"unsafe or invalid {label} segment")
    cleaned = value.strip()
    if not _is_safe_segment(cleaned):
        raise StorageKeyError(f"unsafe or invalid {label} segment")
    return cleaned


def _require_bounded_int(value: object, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise StorageKeyError(f"{label} out of bounds ({low}..{high})")
    return value


def bronze_object_key(*, tenant_id: uuid.UUID, sha256: str) -> str:
    """Build immutable bronze-layer key partitioned by tenant_id and SHA-256 fanout prefix."""
    digest = _validate_sha256(sha256)
    return f"bronze/{_require_uuid(tenant_id, 'tenant_id')}/{digest[:2]}/{digest}"


def silver_figure_key(
    *,
    tenant_id: uuid.UUID,
    document_version_id: uuid.UUID,
    page_no: int,
    region_id: str,
    ext: str = "png",
) -> str:
    """Build silver-layer cropped figure/table image key."""
    page = _require_bounded_int(page_no, "page_no", 1, 9999)
    if not isinstance(ext, str):
        raise StorageKeyError("unsupported figure extension")
    clean_ext = ext.strip().lower().lstrip(".")
    if clean_ext not in _ALLOWED_FIGURE_EXTS:
        raise StorageKeyError("unsupported figure extension")
    safe_region = _validate_segment(region_id, "region_id")
    tenant = _require_uuid(tenant_id, "tenant_id")
    version = _require_uuid(document_version_id, "document_version_id")
    return f"silver/{tenant}/{version}/p{page:04d}_{safe_region}.{clean_ext}"


def preview_pdf_key(
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    sha256: str,
    preview_pages: int = 2,
) -> str:
    """Build key for limited-page PREVIEW_PDF slices shown to FREE-tier learners."""
    pages = _require_bounded_int(preview_pages, "preview_pages", 1, 10)
    digest = _validate_sha256(sha256)
    tenant = _require_uuid(tenant_id, "tenant_id")
    asset = _require_uuid(asset_id, "asset_id")
    return f"previews/{tenant}/{asset}/{digest}_p{pages}.pdf"


def quarantine_object_key(*, tenant_id: uuid.UUID, job_id: str, sha256: str) -> str:
    """Build isolated key for infected or rejected uploads held out of active layers."""
    safe_job = _validate_segment(job_id, "job_id")
    digest = _validate_sha256(sha256)
    return f"quarantine/{_require_uuid(tenant_id, 'tenant_id')}/{safe_job}/{digest}"


def verify_object_key_tenant_ownership(*, object_key: str, expected_tenant_id: uuid.UUID) -> None:
    """Ensure every path segment is normalized and scoped to expected_tenant_id."""
    expected = _require_uuid(expected_tenant_id, "expected_tenant_id")
    if not isinstance(object_key, str) or not object_key or len(object_key) > 1024:
        raise StorageKeyError("object_key contains illegal characters")
    parts = object_key.split("/")
    if len(parts) < 3 or any(not _is_safe_segment(p) for p in parts):
        raise StorageKeyError("malformed or traversal object_key")
    layer, tenant_part = parts[0], parts[1]
    if layer not in _STORAGE_LAYERS:
        raise StorageKeyError("unknown storage layer")
    try:
        key_tenant = uuid.UUID(tenant_part)
    except ValueError as exc:
        raise StorageKeyError("object_key missing valid tenant UUID segment") from exc
    # Only the canonical lowercase hyphenated form is accepted: braces, urn: prefixes, upper case
    # and unhyphenated forms would otherwise alias the same tenant under different strings.
    if str(key_tenant) != tenant_part:
        raise StorageKeyError("object_key tenant segment is not a canonical UUID")
    if key_tenant != expected:
        raise StorageKeyError("object_key tenant segment does not match expected_tenant_id")


def sanitize_download_filename(raw_name: str, *, fallback: str = "document.bin") -> str:
    """Strip path separators, quotes, and control chars for RFC 6266 Content-Disposition."""
    if not isinstance(raw_name, str):
        return fallback
    base = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    if not cleaned or ".." in cleaned:
        return fallback
    return cleaned[:120]


def validate_mime_type(mime_type: object) -> str:
    """Accept only a bare lowercase ``type/subtype`` token; reject parameters, CR/LF and non-ASCII."""
    if not isinstance(mime_type, str):
        raise StorageKeyError("invalid mime_type")
    cleaned = mime_type.strip().lower()
    if _MIME_RE.fullmatch(cleaned) is None:
        raise StorageKeyError("invalid mime_type")
    return cleaned


def build_download_response_headers(
    *,
    filename: str,
    mime_type: str,
    inline: bool = False,
    public_immutable: bool = False,
) -> dict[str, str]:
    """Produce hardened HTTP headers preventing MIME sniffing and header injection on asset downloads."""
    safe_name = sanitize_download_filename(filename)
    content_type = validate_mime_type(mime_type)
    disposition = "inline" if inline else "attachment"
    cache_control = "public, max-age=31536000, immutable" if public_immutable else "private, no-store"
    headers = {
        "Content-Type": content_type,
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": cache_control,
    }
    if inline:
        # Inline rendering of user-supplied files must never run script or load subresources.
        headers["Content-Security-Policy"] = _INLINE_CSP
    return headers
