"""Public API for app.modules.audit (G02 Legal Takedown, immutable audit event builder, and rollback diff)."""

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
from app.modules.audit.internal.takedown import (
    TakedownCaseState,
    TakedownEntityType,
    TakedownExecutionResult,
    TakedownStatus,
    execute_takedown,
    reject_takedown,
)

__all__ = [
    "MIN_SENSITIVE_REASON_LEN",
    "SENSITIVE_AUDIT_ACTIONS",
    "AuditDiffError",
    "AuditEventSpec",
    "RollbackPlan",
    "TakedownCaseState",
    "TakedownEntityType",
    "TakedownExecutionResult",
    "TakedownStatus",
    "build_audit_event_record",
    "compute_rollback_patch",
    "execute_takedown",
    "monthly_audit_partition_name",
    "reject_takedown",
]
