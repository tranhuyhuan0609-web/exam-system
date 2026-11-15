"""Public entry point of the drive_sync module (A03)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.drive_sync.internal.safe_archive import (
    ArchiveEntrySummary,
    ArchiveInspectionResult,
    ArchiveSecurityViolationError,
    SafeArchivePolicy,
    extract_zip_safely,
    inspect_zip,
)

if TYPE_CHECKING:
    from app.modules.drive_sync.internal.service import (
        ALLOWED_DRIVE_SCOPES,
        DriveConnectionView,
        DriveFileChange,
        DriveSyncJobView,
        DriveSyncNotFoundError,
        DriveSyncPolicyError,
        DriveSyncValidationError,
        FilePairingView,
        confirm_or_reject_file_pairing,
        connect_google_drive,
        get_drive_connection,
        get_drive_sync_job,
        revoke_drive_connection,
        sync_drive_connection,
        validate_drive_scopes,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "ALLOWED_DRIVE_SCOPES",
        "DriveConnectionView",
        "DriveFileChange",
        "DriveSyncJobView",
        "DriveSyncNotFoundError",
        "DriveSyncPolicyError",
        "DriveSyncValidationError",
        "FilePairingView",
        "confirm_or_reject_file_pairing",
        "connect_google_drive",
        "get_drive_connection",
        "get_drive_sync_job",
        "revoke_drive_connection",
        "sync_drive_connection",
        "validate_drive_scopes",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.drive_sync.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ALLOWED_DRIVE_SCOPES",
    "ArchiveEntrySummary",
    "ArchiveInspectionResult",
    "ArchiveSecurityViolationError",
    "DriveConnectionView",
    "DriveFileChange",
    "DriveSyncJobView",
    "DriveSyncNotFoundError",
    "DriveSyncPolicyError",
    "DriveSyncValidationError",
    "FilePairingView",
    "SafeArchivePolicy",
    "confirm_or_reject_file_pairing",
    "connect_google_drive",
    "extract_zip_safely",
    "get_drive_connection",
    "get_drive_sync_job",
    "inspect_zip",
    "revoke_drive_connection",
    "sync_drive_connection",
    "validate_drive_scopes",
]
