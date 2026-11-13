"""Pure unit tests for F01 Roster, F02 Assignment Gate & F03 Gradebook Analytics (P33)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.modules.classroom.public import (
    AssignmentSchedule,
    ClassMemberState,
    ClassMemberStatus,
    ClassRole,
    ClassroomPolicyError,
    ItemResponseObservation,
    RevealPolicy,
    StudentAttemptGrade,
    assert_can_start_assignment,
    can_reveal_assignment_answers,
    compute_class_analytics,
    filter_active_assignment_recipients,
    render_gradebook_csv,
    soft_leave_class,
)


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (ClassRole, ClassMemberStatus, RevealPolicy):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_soft_leave_preserves_historical_gradebook_but_stops_new_assignments() -> None:
    t0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    alice = ClassMemberState("cls-1", "t-1", "mem-alice", ClassRole.STUDENT, ClassMemberStatus.ACTIVE)
    bob = ClassMemberState("cls-1", "t-1", "mem-bob", ClassRole.STUDENT, ClassMemberStatus.ACTIVE)

    alice_left = soft_leave_class(alice, left_at=t0)
    assert alice_left.status == ClassMemberStatus.INACTIVE
    assert alice_left.left_at == t0

    recipients = filter_active_assignment_recipients([alice_left, bob])
    assert [m.membership_id for m in recipients] == ["mem-bob"]

    # Historical grade of alice remains in gradebook export cleanly
    grades = [
        StudentAttemptGrade("mem-alice", "Alice", 8.5, t0 - timedelta(days=2), t0, True),
        StudentAttemptGrade("mem-bob", "Bob", 9.0, t0 - timedelta(days=1), t0, False),
    ]
    csv_out = render_gradebook_csv(grades)
    assert "mem-alice,Alice,8.50" in csv_out
    assert "mem-bob,Bob,9.00" in csv_out


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


def test_class_analytics_identifies_top_5_hardest_and_top_distractor() -> None:
    t_due = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    grades = [
        StudentAttemptGrade("m1", "S1", 9.0, t_due - timedelta(hours=1), t_due),
        StudentAttemptGrade("m2", "S2", 5.5, t_due - timedelta(minutes=5), t_due),
        StudentAttemptGrade("m3", "S3", 3.5, t_due + timedelta(hours=2), t_due),
    ]
    obs = []
    # q1: 4 wrong out of 5, distractor C chosen 3 times
    obs.extend([ItemResponseObservation("q1", False, "C")] * 3)
    obs.append(ItemResponseObservation("q1", False, "D"))
    obs.append(ItemResponseObservation("q1", True, "A"))
    # q2..q7: varying error rates
    for idx in range(2, 8):
        obs.extend([ItemResponseObservation(f"q{idx}", False, "B")] * (idx % 3))
        obs.extend([ItemResponseObservation(f"q{idx}", True, "A")] * 4)

    summary = compute_class_analytics(grades=grades, item_observations=obs)
    assert summary.total_submissions == 3
    assert summary.on_time_submissions == 2
    assert summary.score_histogram_2pt_bins["[8, 10]"] == 1
    assert len(summary.top_5_hardest_questions) == 5
    hardest = summary.top_5_hardest_questions[0]
    assert hardest.question_id == "q1"
    assert hardest.error_rate == 0.8
    assert hardest.top_wrong_distractor == "C"
    assert hardest.top_wrong_distractor_count == 3
