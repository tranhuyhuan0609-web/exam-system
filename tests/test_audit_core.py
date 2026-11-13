"""Tests for G02/RB-10 immutable audit_events builder and rollback diff (P63)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.audit.public import (
    AuditDiffError,
    build_audit_event_record,
    compute_rollback_patch,
)


def test_build_audit_event_redacts_secrets_and_derives_partition_and_rollback() -> None:
    ts = datetime(2026, 11, 13, 13, 30, tzinfo=timezone.utc)
    spec = build_audit_event_record(
        tenant_id="tenant-1",
        actor_id="admin-9",
        correlation_id="corr-xyz",
        action="CHANGE_RIGHTS_STATUS",
        target_table="questions",
        target_id="q-101",
        reason="Xac nhan ban quyen NXB GDVN ngay 13/11/2026",
        before_state={"rights_status": "PENDING_REVIEW", "reviewer_email": "a@example.com"},
        after_state={"rights_status": "LICENSED", "reviewer_email": "b@example.com"},
        occurred_at=ts,
    )

    assert spec.partition_name == "audit_events_y2026m11"
    assert spec.changed_fields == ("rights_status",)
    assert spec.before_json["reviewer_email"] == "[REDACTED]"
    assert spec.after_json["reviewer_email"] == "[REDACTED]"

    plan = compute_rollback_patch(spec)
    assert plan.restore_fields == {"rights_status": "PENDING_REVIEW"}
    assert plan.blocked_redacted_fields == ()


def test_sensitive_action_rejects_short_reason_and_missing_correlation() -> None:
    with pytest.raises(AuditDiffError, match="15"):
        build_audit_event_record(
            tenant_id="t1",
            actor_id="u1",
            correlation_id="corr-1",
            action="EXECUTE_TAKEDOWN",
            target_table="content_items",
            target_id="c-1",
            reason="too short",
            before_state={"publication_status": "PUBLISHED"},
            after_state={"publication_status": "TAKEN_DOWN"},
        )

    with pytest.raises(AuditDiffError, match="correlation_id"):
        build_audit_event_record(
            tenant_id="t1",
            actor_id="u1",
            correlation_id=" ",
            action="CREATE_ITEM",
            target_table="content_items",
            target_id="c-1",
            reason="Initial creation of learning item",
            before_state={},
            after_state={"title": "Bai 1"},
        )
