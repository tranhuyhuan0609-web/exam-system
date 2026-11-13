"""Public API for app.modules.audit (G02 immutable audit event builder and safe rollback diff)."""

from app.modules.audit.internal.diff import (
    MIN_SENSITIVE_REASON_LEN,
    SENSITIVE_AUDIT_ACTIONS,
    AuditDiffError,
    AuditEventSpec,
    RollbackPlan,
    build_audit_event_record,
    compute_rollback_patch,
    monthly_audit_partition_name,
)

__all__ = [
    "MIN_SENSITIVE_REASON_LEN",
    "SENSITIVE_AUDIT_ACTIONS",
    "AuditDiffError",
    "AuditEventSpec",
    "RollbackPlan",
    "build_audit_event_record",
    "compute_rollback_patch",
    "monthly_audit_partition_name",
]
