"""Pure unit tests for G02 Legal Takedown (module audit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.audit.public import (
    TakedownCaseState,
    TakedownEntityType,
    TakedownStatus,
    execute_takedown,
    reject_takedown,
)
from app.modules.review.public import GovernancePolicyError, PublicationStatus, RightsStatus


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
