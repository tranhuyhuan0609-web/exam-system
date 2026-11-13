"""A09 review queue task & decision state machine mapped to DDL CHECK constraints."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from app.modules.review.internal.publish_gate import GovernancePolicyError, VerificationStatus


class ReviewEntityType(StrEnum):
    """Exact DDL CHECK values for `review_tasks.entity_type`."""

    QUESTION_CANDIDATE = "QUESTION_CANDIDATE"
    QUESTION_VERSION = "QUESTION_VERSION"
    EXAM_ARCHIVE = "EXAM_ARCHIVE"
    CONTENT_VERSION = "CONTENT_VERSION"


class ReviewSeverity(StrEnum):
    """Exact DDL CHECK values for `review_tasks.severity`."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReviewTaskStatus(StrEnum):
    """Exact DDL CHECK values for `review_tasks.status`."""

    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class ReviewDecisionType(StrEnum):
    """Exact DDL CHECK values for `review_decisions.decision`."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REPROCESS = "REQUEST_REPROCESS"
    SPLIT_MERGE = "SPLIT_MERGE"


_DECISION_TO_VERIFICATION: dict[ReviewDecisionType, VerificationStatus | None] = {
    ReviewDecisionType.APPROVE: VerificationStatus.VERIFIED,
    ReviewDecisionType.REJECT: VerificationStatus.REJECTED,
    ReviewDecisionType.REQUEST_REPROCESS: VerificationStatus.DISPUTED,
    ReviewDecisionType.SPLIT_MERGE: None,
}


@dataclass(frozen=True, slots=True)
class RecordedReviewDecision:
    decision_id: str
    reviewer_id: str
    decision: ReviewDecisionType
    reason: str
    resulting_verification_status: VerificationStatus | None


@dataclass(frozen=True, slots=True)
class ReviewTaskState:
    """Immutable state snapshot of an A09 `review_tasks` workflow item."""

    task_id: str
    tenant_id: str
    entity_type: ReviewEntityType
    entity_id: str
    queue_name: str
    severity: ReviewSeverity = ReviewSeverity.NORMAL
    status: ReviewTaskStatus = ReviewTaskStatus.OPEN
    assigned_to: str | None = None
    decisions: tuple[RecordedReviewDecision, ...] = ()
    applied_decision_ids: frozenset[str] = field(default_factory=frozenset)


def claim_review_task(state: ReviewTaskState, *, reviewer_id: str) -> ReviewTaskState:
    """Assign an `OPEN` review task to a human reviewer (`IN_REVIEW`)."""
    if not reviewer_id:
        raise GovernancePolicyError("MISSING_REVIEWER_ID", "reviewer_id must be non-empty")
    if state.status in {ReviewTaskStatus.RESOLVED, ReviewTaskStatus.CANCELLED}:
        raise GovernancePolicyError(
            "REVIEW_TASK_CLOSED",
            f"Cannot claim task in terminal status {state.status.value}",
        )
    if state.assigned_to == reviewer_id and state.status == ReviewTaskStatus.IN_REVIEW:
        return state
    return replace(state, status=ReviewTaskStatus.IN_REVIEW, assigned_to=reviewer_id)


def submit_review_decision(
    state: ReviewTaskState,
    *,
    decision_id: str,
    reviewer_id: str,
    decision: ReviewDecisionType,
    reason: str,
) -> ReviewTaskState:
    """Idempotently record a reviewer's decision and close the review task as `RESOLVED`."""
    if not decision_id:
        raise GovernancePolicyError("MISSING_DECISION_ID", "decision_id must be non-empty")
    if not reviewer_id:
        raise GovernancePolicyError("MISSING_REVIEWER_ID", "reviewer_id must be non-empty")
    if not reason.strip():
        raise GovernancePolicyError("MISSING_DECISION_REASON", "Review decision requires non-empty reason")

    if decision_id in state.applied_decision_ids:
        return state

    if state.status in {ReviewTaskStatus.RESOLVED, ReviewTaskStatus.CANCELLED}:
        raise GovernancePolicyError(
            "REVIEW_TASK_CLOSED",
            f"Cannot submit decision for closed task ({state.status.value})",
        )

    recorded = RecordedReviewDecision(
        decision_id=decision_id,
        reviewer_id=reviewer_id,
        decision=decision,
        reason=reason.strip(),
        resulting_verification_status=_DECISION_TO_VERIFICATION[decision],
    )
    return replace(
        state,
        status=ReviewTaskStatus.RESOLVED,
        assigned_to=reviewer_id,
        decisions=state.decisions + (recorded,),
        applied_decision_ids=state.applied_decision_ids | {decision_id},
    )


def cancel_review_task(state: ReviewTaskState) -> ReviewTaskState:
    """Cancel an unresolved review task."""
    if state.status == ReviewTaskStatus.CANCELLED:
        return state
    if state.status == ReviewTaskStatus.RESOLVED:
        raise GovernancePolicyError("REVIEW_TASK_CLOSED", "Cannot cancel an already RESOLVED review task")
    return replace(state, status=ReviewTaskStatus.CANCELLED)
