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
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be an integer >= 1")
        for stamp in (self.available_from, self.due_at):
            if stamp is not None and (not isinstance(stamp, datetime) or stamp.tzinfo is None):
                raise ValueError("available_from and due_at must be timezone-aware datetimes")
        if self.due_at is not None and self.due_at < self.available_from:
            raise ValueError("due_at cannot be earlier than available_from")
        if not isinstance(self.reveal_policy, RevealPolicy):
            raise ValueError("reveal_policy must be a RevealPolicy member")
        if not isinstance(self.manually_released, bool):
            raise ValueError("manually_released must be a bool")


def _require_aware(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ClassroomPolicyError("NAIVE_TIMESTAMP", "now must be a timezone-aware datetime")


def assert_can_start_assignment(
    *,
    member: ClassMemberState,
    schedule: AssignmentSchedule,
    attempts_used: int,
    now: datetime,
    allow_late_submission: bool = False,
) -> None:
    """Enforce F01 membership status + F02 `available_from`, `due_at`, and `max_attempts` gates."""
    _require_aware(now)
    if isinstance(attempts_used, bool) or not isinstance(attempts_used, int) or attempts_used < 0:
        raise ClassroomPolicyError("INVALID_ATTEMPTS_USED", "attempts_used must be a non-negative integer")
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
    _require_aware(now)
    if learner_submitted is not True:
        return False
    if schedule.reveal_policy == RevealPolicy.IMMEDIATE:
        return True
    if schedule.reveal_policy == RevealPolicy.AFTER_DUE:
        return schedule.due_at is not None and now >= schedule.due_at
    if schedule.reveal_policy == RevealPolicy.MANUAL_RELEASE:
        return schedule.manually_released
    return False  # RevealPolicy.NEVER
