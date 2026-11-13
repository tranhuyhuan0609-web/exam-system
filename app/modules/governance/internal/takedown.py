"""G02 Legal Takedown case state machine and immutable audit delta builders."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from app.modules.governance.internal.publish_gate import GovernancePolicyError, PublicationStatus, RightsStatus


class TakedownEntityType(StrEnum):
    """Exact DDL CHECK values for `takedown_cases.target_entity_type`."""

    SOURCE_DOCUMENT = "SOURCE_DOCUMENT"
    CONTENT_ITEM = "CONTENT_ITEM"
    QUESTION = "QUESTION"


class TakedownStatus(StrEnum):
    """Exact DDL CHECK values for `takedown_cases.status`."""

    OPEN = "OPEN"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class TakedownCaseState:
    """Immutable state of a G02 `takedown_cases` record."""

    case_id: str
    tenant_id: str
    complainant_name: str
    target_entity_type: TakedownEntityType
    target_entity_id: str
    legal_reason: str
    status: TakedownStatus = TakedownStatus.OPEN
    executed_by: str | None = None


@dataclass(frozen=True, slots=True)
class TakedownExecutionResult:
    """Outcome containing updated case state, forced takedown statuses, event payload, and audit diff."""

    case_state: TakedownCaseState
    forced_rights_status: RightsStatus
    forced_publication_status: PublicationStatus
    outbox_event_type: str
    outbox_payload: dict[str, Any]
    audit_before_json: dict[str, Any]
    audit_after_json: dict[str, Any]


def execute_takedown(
    state: TakedownCaseState,
    *,
    executor_id: str,
    previous_rights_status: RightsStatus,
    previous_publication_status: PublicationStatus,
) -> TakedownExecutionResult:
    """Transition an `OPEN` takedown case to `EXECUTED` and build propagation + audit payloads."""
    if not executor_id:
        raise GovernancePolicyError("MISSING_EXECUTOR_ID", "executor_id must be non-empty")
    if state.status != TakedownStatus.OPEN:
        raise GovernancePolicyError(
            "TAKEDOWN_NOT_OPEN",
            f"Only OPEN takedown cases can be executed, got {state.status.value}",
        )

    next_state = replace(state, status=TakedownStatus.EXECUTED, executed_by=executor_id)
    event_payload: dict[str, Any] = {
        "case_id": state.case_id,
        "tenant_id": state.tenant_id,
        "target_entity_type": state.target_entity_type.value,
        "target_entity_id": state.target_entity_id,
        "legal_reason": state.legal_reason,
        "executed_by": executor_id,
        "rights_status": RightsStatus.TAKEDOWN.value,
        "publication_status": PublicationStatus.TAKEN_DOWN.value,
    }
    return TakedownExecutionResult(
        case_state=next_state,
        forced_rights_status=RightsStatus.TAKEDOWN,
        forced_publication_status=PublicationStatus.TAKEN_DOWN,
        outbox_event_type="rights.takedown.executed",
        outbox_payload=event_payload,
        audit_before_json={
            "case_status": state.status.value,
            "rights_status": previous_rights_status.value,
            "publication_status": previous_publication_status.value,
        },
        audit_after_json={
            "case_status": TakedownStatus.EXECUTED.value,
            "rights_status": RightsStatus.TAKEDOWN.value,
            "publication_status": PublicationStatus.TAKEN_DOWN.value,
        },
    )


def reject_takedown(state: TakedownCaseState, *, reviewer_id: str) -> TakedownCaseState:
    """Reject an unsubstantiated `OPEN` takedown claim."""
    if not reviewer_id:
        raise GovernancePolicyError("MISSING_EXECUTOR_ID", "reviewer_id must be non-empty")
    if state.status != TakedownStatus.OPEN:
        raise GovernancePolicyError(
            "TAKEDOWN_NOT_OPEN",
            f"Only OPEN takedown cases can be rejected, got {state.status.value}",
        )
    return replace(state, status=TakedownStatus.REJECTED, executed_by=reviewer_id)
