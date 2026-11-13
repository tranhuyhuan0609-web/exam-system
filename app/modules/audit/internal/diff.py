"""G02 & RB-10 Immutable audit_events entry builder, partition helper, and safe rollback diff."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SENSITIVE_AUDIT_ACTIONS: frozenset[str] = frozenset(
    {
        "EXECUTE_TAKEDOWN",
        "OVERRIDE_GRADE",
        "CHANGE_RIGHTS_STATUS",
        "REPLAY_DLQ",
        "REVOKE_ROLE",
        "ROTATE_SECRET",
    }
)
MIN_SENSITIVE_REASON_LEN = 15

_SENSITIVE_KEY_TOKENS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "private_key",
    "api_key",
    "jwt",
    "email",
    "phone",
)


class AuditDiffError(ValueError):
    """Raised when an audit event lacks required traceability or safe rollback context."""


@dataclass(frozen=True)
class AuditEventSpec:
    tenant_id: str
    actor_id: str
    correlation_id: str
    action: str
    target_table: str
    target_id: str
    reason: str
    partition_name: str
    changed_fields: tuple[str, ...]
    before_json: dict[str, Any]
    after_json: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True)
class RollbackPlan:
    target_table: str
    target_id: str
    restore_fields: dict[str, Any]
    blocked_redacted_fields: tuple[str, ...]


def _is_sensitive_key(key: str) -> bool:
    norm = key.lower().replace("-", "_").replace(" ", "_")
    return any(tok in norm for tok in _SENSITIVE_KEY_TOKENS)


def _redact_snapshot(obj: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        return "[DEPTH_LIMIT]"
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key_str = str(k)
            if _is_sensitive_key(key_str):
                out[key_str] = "[REDACTED]"
            else:
                out[key_str] = _redact_snapshot(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_redact_snapshot(item, depth=depth + 1) for item in obj]
    return copy.deepcopy(obj)


def monthly_audit_partition_name(ts: datetime) -> str:
    utc_ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return f"audit_events_y{utc_ts.year:04d}m{utc_ts.month:02d}"


def build_audit_event_record(
    *,
    tenant_id: str,
    actor_id: str,
    correlation_id: str,
    action: str,
    target_table: str,
    target_id: str,
    reason: str,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    occurred_at: datetime | None = None,
) -> AuditEventSpec:
    """Build an append-only audit_events payload with redacted snapshots and diff keys."""
    for label, val in (
        ("tenant_id", tenant_id),
        ("actor_id", actor_id),
        ("correlation_id", correlation_id),
        ("action", action),
        ("target_table", target_table),
        ("target_id", target_id),
    ):
        if not val or not val.strip():
            raise AuditDiffError(f"{label} must not be empty")

    norm_action = action.strip().upper()
    norm_reason = " ".join(reason.strip().split())
    if not norm_reason:
        raise AuditDiffError("reason must not be empty")
    if norm_action in SENSITIVE_AUDIT_ACTIONS and len(norm_reason) < MIN_SENSITIVE_REASON_LEN:
        raise AuditDiffError(
            f"action {norm_action} requires audit reason >= {MIN_SENSITIVE_REASON_LEN} characters"
        )

    before_clean = _redact_snapshot(before_state or {})
    after_clean = _redact_snapshot(after_state or {})

    all_keys = sorted(set(before_clean.keys()) | set(after_clean.keys()))
    changed = tuple(k for k in all_keys if before_clean.get(k) != after_clean.get(k))
    ts = datetime.now(timezone.utc) if occurred_at is None else occurred_at

    return AuditEventSpec(
        tenant_id=tenant_id.strip(),
        actor_id=actor_id.strip(),
        correlation_id=correlation_id.strip(),
        action=norm_action,
        target_table=target_table.strip(),
        target_id=target_id.strip(),
        reason=norm_reason,
        partition_name=monthly_audit_partition_name(ts),
        changed_fields=changed,
        before_json=before_clean,
        after_json=after_clean,
        occurred_at=ts,
    )


def compute_rollback_patch(event: AuditEventSpec) -> RollbackPlan:
    """Derive RB-10 rollback patch from ``event.before_json``, blocking redacted fields."""
    if not event.before_json:
        raise AuditDiffError("cannot compute rollback patch without before_json snapshot")

    restore: dict[str, Any] = {}
    blocked: list[str] = []
    for field in event.changed_fields:
        if field not in event.before_json:
            continue
        prev = event.before_json[field]
        if prev == "[REDACTED]":
            blocked.append(field)
        else:
            restore[field] = copy.deepcopy(prev)

    return RollbackPlan(
        target_table=event.target_table,
        target_id=event.target_id,
        restore_fields=restore,
        blocked_redacted_fields=tuple(sorted(blocked)),
    )
