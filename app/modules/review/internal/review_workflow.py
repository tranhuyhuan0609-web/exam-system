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


_OPEN_STATUSES = frozenset({ReviewTaskStatus.OPEN, ReviewTaskStatus.IN_REVIEW})


def claim_review_task(state: ReviewTaskState, *, reviewer_id: str) -> ReviewTaskState:
    """Assign an `OPEN` review task to a human reviewer (`IN_REVIEW`); a claimed task is not stealable."""
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise GovernancePolicyError("MISSING_REVIEWER_ID", "reviewer_id must be non-empty")
    if state.status in {ReviewTaskStatus.RESOLVED, ReviewTaskStatus.CANCELLED}:
        raise GovernancePolicyError(
            "REVIEW_TASK_CLOSED",
            f"Cannot claim task in terminal status {state.status!s}",
        )
    if state.status not in _OPEN_STATUSES:
        raise GovernancePolicyError("REVIEW_TASK_STATUS_UNKNOWN", "Review task has an unrecognised status")
    if state.status == ReviewTaskStatus.IN_REVIEW:
        if state.assigned_to == reviewer_id:
            return state
        raise GovernancePolicyError(
            "REVIEW_TASK_ALREADY_CLAIMED",
            "Review task is already claimed by another reviewer",
        )
    return replace(state, status=ReviewTaskStatus.IN_REVIEW, assigned_to=reviewer_id)


def submit_review_decision(
    state: ReviewTaskState,
    *,
    decision_id: str,
    reviewer_id: str,
    decision: ReviewDecisionType,
    reason: str,
) -> ReviewTaskState:
    """
    Idempotently record the assigned reviewer's decision and close the review task as `RESOLVED`.

    Replaying a `decision_id` is a no-op only when reviewer, decision and reason are identical;
    any difference is a `DECISION_ID_CONFLICT`.
    """
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise GovernancePolicyError("MISSING_DECISION_ID", "decision_id must be non-empty")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise GovernancePolicyError("MISSING_REVIEWER_ID", "reviewer_id must be non-empty")
    if not isinstance(reason, str) or not reason.strip():
        raise GovernancePolicyError("MISSING_DECISION_REASON", "Review decision requires non-empty reason")
    if decision not in _DECISION_TO_VERIFICATION or not isinstance(decision, ReviewDecisionType):
        raise GovernancePolicyError("INVALID_DECISION", "decision must be a ReviewDecisionType member")

    if decision_id in state.applied_decision_ids:
        for existing in state.decisions:
            if existing.decision_id == decision_id:
                if (existing.reviewer_id, existing.decision, existing.reason) == (
                    reviewer_id,
                    decision,
                    reason.strip(),
                ):
                    return state
                break
        raise GovernancePolicyError(
            "DECISION_ID_CONFLICT",
            "decision_id was already applied with different content",
        )

    if state.status in {ReviewTaskStatus.RESOLVED, ReviewTaskStatus.CANCELLED}:
        raise GovernancePolicyError(
            "REVIEW_TASK_CLOSED",
            f"Cannot submit decision for closed task ({state.status!s})",
        )
    if state.status != ReviewTaskStatus.IN_REVIEW:
        raise GovernancePolicyError("REVIEW_TASK_NOT_CLAIMED", "Review task must be claimed before a decision")
    if state.assigned_to != reviewer_id:
        raise GovernancePolicyError(
            "REVIEWER_NOT_ASSIGNED",
            "Only the reviewer who claimed the task may decide it",
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
        decisions=state.decisions + (recorded,),
        applied_decision_ids=state.applied_decision_ids | {decision_id},
    )


def cancel_review_task(state: ReviewTaskState) -> ReviewTaskState:
    """Cancel an unresolved review task."""
    if state.status == ReviewTaskStatus.CANCELLED:
        return state
    if state.status == ReviewTaskStatus.RESOLVED:
        raise GovernancePolicyError("REVIEW_TASK_CLOSED", "Cannot cancel an already RESOLVED review task")
    if state.status not in _OPEN_STATUSES:
        raise GovernancePolicyError("REVIEW_TASK_STATUS_UNKNOWN", "Review task has an unrecognised status")
    return replace(state, status=ReviewTaskStatus.CANCELLED)
