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
