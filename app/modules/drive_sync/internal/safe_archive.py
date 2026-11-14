"""Safe ZIP archive central-directory inspection and sandbox extraction (module A03).

All limits (entry count, directory depth, path traversal, symlinks, compression ratio,
and total uncompressed size) are verified from central-directory metadata BEFORE any
bytes are extracted.
"""

from __future__ import annotations

import os
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SYMLINK_MODE = 0o120000
_TYPE_MASK = 0o170000


class ArchiveSecurityViolationError(ValueError):
    """Raised when an archive violates SafeArchivePolicy limits or path safety."""


@dataclass(frozen=True)
class SafeArchivePolicy:
    """Limits guarding against zip bombs, path traversal, and deep nesting."""

    max_entries: int = 500
    max_total_uncompressed_bytes: int = 200 * 1024 * 1024
    max_compression_ratio: float = 50.0
    max_directory_depth: int = 4
    allowed_extensions: tuple[str, ...] = (".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png")


DEFAULT_POLICY = SafeArchivePolicy()


@dataclass(frozen=True)
class ArchiveEntrySummary:
    """Validated metadata for a single file entry inside a ZIP archive."""

    archive_path: str
    file_size: int
    compress_size: int
    directory_depth: int


@dataclass(frozen=True)
class ArchiveInspectionResult:
    """Outcome of central-directory inspection before extraction."""

    file_count: int
    total_uncompressed_bytes: int
    max_observed_depth: int
    max_observed_ratio: float
    entries: tuple[ArchiveEntrySummary, ...]


def _validate_entry_path(raw_name: str) -> str:
    if not raw_name or "\x00" in raw_name:
        raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: invalid entry name {raw_name!r}")
    if "\\" in raw_name or raw_name.startswith("/"):
        raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: {raw_name}")

    raw_parts = raw_name.rstrip("/").split("/")
    if ".." in raw_parts:
        raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: {raw_name}")
    if raw_parts and ":" in raw_parts[0]:
        raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: {raw_name}")

    normalized = posixpath.normpath(raw_name)
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        raise ArchiveSecurityViolationError(f"PATH_TRAVERSAL_BLOCKED: {raw_name}")
    return normalized


def inspect_zip(
    path: str | Path,
    policy: SafeArchivePolicy = DEFAULT_POLICY,
) -> ArchiveInspectionResult:
    """Inspect a ZIP archive's central directory without extracting any files."""
    zip_path = Path(path)
    entries: list[ArchiveEntrySummary] = []
    total_uncompressed = 0
    max_depth = 0
    max_ratio = 0.0

    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        file_infos = [info for info in infos if not info.is_dir()]
        if len(file_infos) > policy.max_entries:
            raise ArchiveSecurityViolationError(
                f"ZIP_TOO_MANY_FILES: {len(file_infos)} files exceed limit {policy.max_entries}"
            )

        for info in infos:
            unix_mode = info.external_attr >> 16
            if (unix_mode & _TYPE_MASK) == _SYMLINK_MODE:
                raise ArchiveSecurityViolationError(f"SYMLINK_IN_ARCHIVE_BLOCKED: {info.filename}")

            if info.is_dir():
                _validate_entry_path(info.filename)
                continue

            clean_rel = _validate_entry_path(info.filename)
            dir_depth = len(PurePosixPath(clean_rel).parent.parts)
            if dir_depth > policy.max_directory_depth:
                raise ArchiveSecurityViolationError(
                    f"ARCHIVE_DEPTH_EXCEEDED: {clean_rel} has depth {dir_depth} > {policy.max_directory_depth}"
                )

            if info.file_size < 0 or info.compress_size < 0:
                raise ArchiveSecurityViolationError(f"INVALID_ENTRY_SIZE: {info.filename}")

            if info.file_size > 0:
                if info.compress_size == 0:
                    raise ArchiveSecurityViolationError(
                        f"ZIP_BOMB_RATIO_BLOCKED: {info.filename} has zero compressed size"
                    )
                ratio = info.file_size / info.compress_size
                if ratio >= policy.max_compression_ratio:
                    raise ArchiveSecurityViolationError(
                        f"ZIP_BOMB_RATIO_BLOCKED: {info.filename} ratio {ratio:.1f}:1 >= "
                        f"{policy.max_compression_ratio}:1"
                    )
                max_ratio = max(max_ratio, ratio)

            total_uncompressed += info.file_size
            if total_uncompressed > policy.max_total_uncompressed_bytes:
                raise ArchiveSecurityViolationError(
                    f"ZIP_TOTAL_SIZE_EXCEEDED: {total_uncompressed} bytes > {policy.max_total_uncompressed_bytes}"
                )

            max_depth = max(max_depth, dir_depth)
            entries.append(
                ArchiveEntrySummary(
                    archive_path=clean_rel,
                    file_size=info.file_size,
                    compress_size=info.compress_size,
                    directory_depth=dir_depth,
                )
            )

    return ArchiveInspectionResult(
        file_count=len(entries),
        total_uncompressed_bytes=total_uncompressed,
        max_observed_depth=max_depth,
        max_observed_ratio=max_ratio,
        entries=tuple(entries),
    )


def extract_zip_safely(
    zip_path: str | Path,
    target_extract_dir: str | Path,
    policy: SafeArchivePolicy = DEFAULT_POLICY,
) -> tuple[Path, ...]:
    """Validate central directory via ``inspect_zip`` then extract allowed extensions."""
    inspection = inspect_zip(zip_path, policy=policy)
    os.makedirs(target_extract_dir, exist_ok=True)
    resolved_root = Path(target_extract_dir).resolve()
    extracted: list[Path] = []
    allowed = {ext.lower() for ext in policy.allowed_extensions}
    total_written = 0

    with zipfile.ZipFile(Path(zip_path), "r") as zf:
        entry_by_path = {e.archive_path: e for e in inspection.entries}
        for info in zf.infolist():
            if info.is_dir():
                continue
            clean_rel = _validate_entry_path(info.filename)
            if clean_rel not in entry_by_path:
                continue
            if PurePosixPath(clean_rel).suffix.lower() not in allowed:
                continue

            dest_path = (resolved_root / clean_rel).resolve()
            if not dest_path.is_relative_to(resolved_root):
                raise ArchiveSecurityViolationError(f"ESCAPE_SANDBOX_BLOCKED: {dest_path}")

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src_stream, open(dest_path, "wb") as dst_file:
                while chunk := src_stream.read(65536):
                    total_written += len(chunk)
                    if total_written > policy.max_total_uncompressed_bytes:
                        raise ArchiveSecurityViolationError("ZIP_TOTAL_SIZE_EXCEEDED: actual stream exceeds limit")
                    dst_file.write(chunk)
            extracted.append(dest_path)

    return tuple(extracted)
