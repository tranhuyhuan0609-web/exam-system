"""F02 assignment schedule + start/reveal guards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.modules.classes.public import ClassMemberState, ClassMemberStatus, ClassroomPolicyError


class RevealPolicy(StrEnum):
    """Exact DDL CHECK values for `assessment_assignments.reveal_policy`."""

    IMMEDIATE = "IMMEDIATE"
    AFTER_DUE = "AFTER_DUE"
    MANUAL_RELEASE = "MANUAL_RELEASE"
    NEVER = "NEVER"


@dataclass(frozen=True, slots=True)
class AssignmentSchedule:
    assignment_id: str
    available_from: datetime
    due_at: datetime | None
    max_attempts: int
    reveal_policy: RevealPolicy = RevealPolicy.AFTER_DUE
    manually_released: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.due_at is not None and self.due_at < self.available_from:
            raise ValueError("due_at cannot be earlier than available_from")


def assert_can_start_assignment(
    *,
    member: ClassMemberState,
    schedule: AssignmentSchedule,
    attempts_used: int,
    now: datetime,
    allow_late_submission: bool = False,
) -> None:
    """Enforce F01 membership status + F02 `available_from`, `due_at`, and `max_attempts` gates."""
    if member.status != ClassMemberStatus.ACTIVE:
        raise ClassroomPolicyError(
            "MEMBER_INACTIVE",
            "Inactive class members cannot start new assignments",
        )
    if now < schedule.available_from:
        raise ClassroomPolicyError(
            "ASSIGNMENT_NOT_OPEN_YET",
            f"Assignment opens at {schedule.available_from.isoformat()}",
        )
    if not allow_late_submission and schedule.due_at is not None and now > schedule.due_at:
        raise ClassroomPolicyError(
            "ASSIGNMENT_PAST_DUE",
            f"Assignment due_at {schedule.due_at.isoformat()} has passed",
        )
    if attempts_used >= schedule.max_attempts:
        raise ClassroomPolicyError(
            "MAX_ATTEMPTS_EXCEEDED",
            f"Used {attempts_used}/{schedule.max_attempts} allowed attempts",
        )


def can_reveal_assignment_answers(
    schedule: AssignmentSchedule,
    *,
    learner_submitted: bool,
    now: datetime,
) -> bool:
    """Evaluate F02 `reveal_policy` strictly across all 4 policy variants."""
    if not learner_submitted:
        return False
    if schedule.reveal_policy == RevealPolicy.IMMEDIATE:
        return True
    if schedule.reveal_policy == RevealPolicy.AFTER_DUE:
        return schedule.due_at is not None and now >= schedule.due_at
    if schedule.reveal_policy == RevealPolicy.MANUAL_RELEASE:
        return schedule.manually_released
    return False  # RevealPolicy.NEVER
