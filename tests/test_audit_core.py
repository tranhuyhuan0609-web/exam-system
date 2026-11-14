"""Tests for G02 Legal Takedown and immutable audit_events builder/rollback diff (module audit)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.audit.public import (
    AuditDiffError,
    TakedownCaseState,
    TakedownEntityType,
    TakedownStatus,
    build_audit_event_record,
    compute_rollback_patch,
    execute_takedown,
    monthly_audit_partition_name,
    reject_takedown,
)
from app.modules.review.public import GovernancePolicyError, PublicationStatus, RightsStatus


def test_build_audit_event_redacts_secrets_and_derives_partition_and_rollback() -> None:
    ts = datetime(2026, 11, 13, 13, 30, tzinfo=UTC)
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
    assert spec.changed_fields == ("reviewer_email", "rights_status")
    assert spec.before_json["reviewer_email"] == "[REDACTED]"
    assert spec.after_json["reviewer_email"] == "[REDACTED]"

    plan = compute_rollback_patch(spec)
    assert plan.restore_fields == {"rights_status": "PENDING_REVIEW"}
    assert plan.blocked_redacted_fields == ("reviewer_email",)


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


# --- regression + boundary tests --------------------------------------------------------------

_TS = datetime(2026, 11, 13, 13, 30, tzinfo=UTC)


def _build(**overrides):
    kwargs = dict(
        tenant_id="t1",
        actor_id="u1",
        correlation_id="corr-1",
        action="UPDATE_SETTINGS",
        target_table="tenant_settings",
        target_id="s-1",
        reason="Rotate integration credentials",
        before_state={},
        after_state={},
        occurred_at=_TS,
    )
    kwargs.update(overrides)
    return build_audit_event_record(**kwargs)


def test_changed_redacted_field_is_listed_as_changed_and_values_stay_redacted() -> None:
    spec = _build(
        before_state={"api_key": "old-key-123", "plan": "FREE"},
        after_state={"api_key": "new-key-456", "plan": "FREE"},
    )
    assert spec.changed_fields == ("api_key",)
    assert spec.before_json["api_key"] == "[REDACTED]"
    assert spec.after_json["api_key"] == "[REDACTED]"
    assert "old-key-123" not in repr(spec) and "new-key-456" not in repr(spec)


def test_changed_email_is_listed_and_rollback_blocks_it() -> None:
    spec = _build(
        before_state={"email": "a@example.com", "status": "ACTIVE"},
        after_state={"email": "b@example.com", "status": "SUSPENDED"},
    )
    assert spec.changed_fields == ("email", "status")
    plan = compute_rollback_patch(spec)
    assert plan.blocked_redacted_fields == ("email",)
    assert plan.restore_fields == {"status": "ACTIVE"}


def test_unchanged_secret_is_not_listed_as_changed() -> None:
    spec = _build(before_state={"api_key": "same", "n": 1}, after_state={"api_key": "same", "n": 2})
    assert spec.changed_fields == ("n",)


def test_added_and_removed_secret_keys_count_as_changed() -> None:
    spec = _build(before_state={"password": "x"}, after_state={"token": "y"})
    assert spec.changed_fields == ("password", "token")


def test_redaction_reaches_dicts_inside_lists_and_tuples() -> None:
    spec = _build(
        before_state={"items": [{"password": "p1"}], "pair": ({"secret": "s1"},)},
        after_state={"items": [{"password": "p2"}], "pair": ({"secret": "s2"},)},
    )
    dumped = repr((spec.before_json, spec.after_json))
    for leaked in ("p1", "p2", "s1", "s2"):
        assert leaked not in dumped
    assert spec.changed_fields == ("items", "pair")


def test_sensitive_key_matching_is_case_and_separator_insensitive() -> None:
    spec = _build(before_state={"Refresh-Token": "a", "PRIVATE KEY": "b"}, after_state={})
    assert spec.before_json == {"Refresh-Token": "[REDACTED]", "PRIVATE KEY": "[REDACTED]"}


def test_deep_nesting_is_cut_off_instead_of_recursing_forever() -> None:
    deep: dict = {"v": 1}
    for _ in range(40):
        deep = {"n": deep}
    spec = _build(before_state={}, after_state=deep)
    assert "[DEPTH_LIMIT]" in repr(spec.after_json)


def test_none_and_blank_required_fields_raise_audit_error_not_attribute_error() -> None:
    for field in ("tenant_id", "actor_id", "correlation_id", "action", "target_table", "target_id"):
        for bad in (None, "", "   "):
            with pytest.raises(AuditDiffError, match=field):
                _build(**{field: bad})
    with pytest.raises(AuditDiffError, match="reason"):
        _build(reason=None)
    with pytest.raises(AuditDiffError, match="reason"):
        _build(reason="   ")


def test_non_mapping_state_is_rejected_with_audit_error() -> None:
    with pytest.raises(AuditDiffError):
        _build(before_state=["x"])  # type: ignore[arg-type]


def test_sensitive_reason_boundary_and_action_normalisation() -> None:
    ok = _build(action=" override_grade ", reason="x" * 15)
    assert ok.action == "OVERRIDE_GRADE"
    with pytest.raises(AuditDiffError):
        _build(action="override_grade", reason="x" * 14)
    # Whitespace collapses before the length check.
    with pytest.raises(AuditDiffError):
        _build(action="REVOKE_ROLE", reason="a      b       c")


def test_partition_name_handles_naive_and_offset_timestamps() -> None:
    assert monthly_audit_partition_name(datetime(2026, 1, 31, 23, 59, 59)) == "audit_events_y2026m01"
    # 2026-12-31 20:00 UTC-5 is already January 2027 in UTC.
    tz = timezone(timedelta(hours=-5))
    assert monthly_audit_partition_name(datetime(2026, 12, 31, 20, 0, tzinfo=tz)) == "audit_events_y2027m01"


def test_rollback_requires_before_snapshot() -> None:
    spec = _build(before_state={}, after_state={"a": 1})
    with pytest.raises(AuditDiffError):
        compute_rollback_patch(spec)


def test_rollback_skips_fields_absent_from_before_and_deep_copies() -> None:
    spec = _build(before_state={"cfg": {"a": [1]}}, after_state={"cfg": {"a": [2]}, "new": 1})
    plan = compute_rollback_patch(spec)
    assert plan.restore_fields == {"cfg": {"a": [1]}}
    plan.restore_fields["cfg"]["a"].append(9)
    assert spec.before_json["cfg"]["a"] == [1]


def test_takedown_enums_match_postgresql_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (TakedownEntityType, TakedownStatus):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_takedown_execution_produces_event_and_immutable_audit_delta() -> None:
    case = TakedownCaseState(
        case_id="td-100",
        tenant_id="tenant-1",
        complainant_name="Publisher X",
        target_entity_type=TakedownEntityType.QUESTION,
        target_entity_id="q-999",
        legal_reason="Copyright notice #2026-11",
    )
    outcome = execute_takedown(
        case,
        executor_id="admin-1",
        previous_rights_status=RightsStatus.LICENSED,
        previous_publication_status=PublicationStatus.PUBLISHED,
    )
    assert outcome.case_state.status == TakedownStatus.EXECUTED
    assert outcome.forced_rights_status == RightsStatus.TAKEDOWN
    assert outcome.forced_publication_status == PublicationStatus.TAKEN_DOWN
    assert outcome.outbox_event_type == "rights.takedown.executed"
    assert outcome.outbox_payload["target_entity_id"] == "q-999"
    assert outcome.audit_before_json["publication_status"] == "PUBLISHED"
    assert outcome.audit_after_json["publication_status"] == "TAKEN_DOWN"

    # Cannot execute or reject after EXECUTED
    with pytest.raises(GovernancePolicyError) as exc:
        reject_takedown(outcome.case_state, reviewer_id="admin-2")
    assert exc.value.code == "TAKEDOWN_NOT_OPEN"


def _case(**kw: object) -> TakedownCaseState:
    base: dict[str, object] = {
        "case_id": "td-1",
        "tenant_id": "tenant-1",
        "complainant_name": "Publisher",
        "target_entity_type": TakedownEntityType.CONTENT_ITEM,
        "target_entity_id": "c-1",
        "legal_reason": "notice",
    }
    base.update(kw)
    return TakedownCaseState(**base)  # type: ignore[arg-type]


def _execute(state: TakedownCaseState, **kw: object):
    args: dict[str, object] = {
        "executor_id": "admin",
        "previous_rights_status": RightsStatus.OWNED,
        "previous_publication_status": PublicationStatus.PUBLISHED,
    }
    args.update(kw)
    return execute_takedown(state, **args)  # type: ignore[arg-type]


def test_execute_requires_open_case_and_non_blank_executor() -> None:
    for executor in ("", "   ", None):
        with pytest.raises(GovernancePolicyError) as exc:
            _execute(_case(), executor_id=executor)
        assert exc.value.code == "MISSING_EXECUTOR_ID"
    done = _execute(_case()).case_state
    for state_change in (done, _case(status=TakedownStatus.REJECTED)):
        with pytest.raises(GovernancePolicyError) as exc:
            _execute(state_change)
        assert exc.value.code == "TAKEDOWN_NOT_OPEN"


def test_unknown_case_status_is_not_executable() -> None:
    with pytest.raises(GovernancePolicyError) as exc:
        _execute(_case(status="open"))
    assert exc.value.code == "TAKEDOWN_NOT_OPEN"


def test_execution_does_not_mutate_input_and_records_actor() -> None:
    case = _case()
    result = _execute(case, executor_id="admin-7")
    assert case.status == TakedownStatus.OPEN and case.executed_by is None
    assert result.case_state.executed_by == "admin-7"
    assert result.outbox_payload["tenant_id"] == "tenant-1"
    assert result.outbox_payload["rights_status"] == "TAKEDOWN"
    assert result.audit_before_json["case_status"] == "OPEN"
    assert result.audit_after_json["case_status"] == "EXECUTED"


def test_reject_open_case_and_guards() -> None:
    rejected = reject_takedown(_case(), reviewer_id="admin")
    assert rejected.status == TakedownStatus.REJECTED and rejected.executed_by == "admin"
    with pytest.raises(GovernancePolicyError) as blank:
        reject_takedown(_case(), reviewer_id=" ")
    assert blank.value.code == "MISSING_EXECUTOR_ID"
    with pytest.raises(GovernancePolicyError) as again:
        reject_takedown(rejected, reviewer_id="admin")
    assert again.value.code == "TAKEDOWN_NOT_OPEN"
    with pytest.raises(GovernancePolicyError):
        _execute(rejected)
