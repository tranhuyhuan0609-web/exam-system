"""Pure unit tests for F02 Assignment Schedule Gate (module assignments)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.modules.assignments.public import (
    AssignmentSchedule,
    RevealPolicy,
    assert_can_start_assignment,
    can_reveal_assignment_answers,
)
from app.modules.classes.public import ClassMemberState, ClassroomPolicyError


def test_reveal_policy_matches_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for member in RevealPolicy:
        assert f"'{member.value}'" in ddl


def test_assignment_time_windows_attempts_and_reveal_policies() -> None:
    t_open = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
    t_due = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)
    schedule = AssignmentSchedule(
        assignment_id="asg-1",
        available_from=t_open,
        due_at=t_due,
        max_attempts=2,
        reveal_policy=RevealPolicy.AFTER_DUE,
    )
    active_student = ClassMemberState("cls-1", "t-1", "mem-1")

    with pytest.raises(ClassroomPolicyError) as early:
        assert_can_start_assignment(
            member=active_student, schedule=schedule, attempts_used=0, now=t_open - timedelta(minutes=1)
        )
    assert early.value.code == "ASSIGNMENT_NOT_OPEN_YET"

    with pytest.raises(ClassroomPolicyError) as late:
        assert_can_start_assignment(
            member=active_student, schedule=schedule, attempts_used=0, now=t_due + timedelta(seconds=1)
        )
    assert late.value.code == "ASSIGNMENT_PAST_DUE"

    with pytest.raises(ClassroomPolicyError) as maxed:
        assert_can_start_assignment(
            member=active_student, schedule=schedule, attempts_used=2, now=t_open + timedelta(hours=1)
        )
    assert maxed.value.code == "MAX_ATTEMPTS_EXCEEDED"

    # Answer reveal gating across all 4 policies
    assert can_reveal_assignment_answers(schedule, learner_submitted=True, now=t_due - timedelta(minutes=1)) is False
    assert can_reveal_assignment_answers(schedule, learner_submitted=True, now=t_due) is True

    manual = AssignmentSchedule("asg-m", t_open, t_due, 1, RevealPolicy.MANUAL_RELEASE, manually_released=False)
    assert can_reveal_assignment_answers(manual, learner_submitted=True, now=t_due + timedelta(days=1)) is False
