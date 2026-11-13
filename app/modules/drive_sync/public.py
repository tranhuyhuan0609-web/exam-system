"""Public entry point of the drive_sync module (A03)."""

from __future__ import annotations

from app.modules.drive_sync.internal.safe_archive import (
    ArchiveEntrySummary,
    ArchiveInspectionResult,
    ArchiveSecurityViolationError,
    SafeArchivePolicy,
    extract_zip_safely,
    inspect_zip,
)

__all__ = [
    "ArchiveEntrySummary",
    "ArchiveInspectionResult",
    "ArchiveSecurityViolationError",
    "SafeArchivePolicy",
    "extract_zip_safely",
    "inspect_zip",
]
