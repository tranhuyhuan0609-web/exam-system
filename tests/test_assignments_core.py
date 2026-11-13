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
from app.modules.classes.public import ClassMemberState, ClassMemberStatus, ClassroomPolicyError


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


_OPEN = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
_DUE = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)


def _sched(**kw: object) -> AssignmentSchedule:
    base: dict[str, object] = {"assignment_id": "a", "available_from": _OPEN, "due_at": _DUE, "max_attempts": 2}
    base.update(kw)
    return AssignmentSchedule(**base)  # type: ignore[arg-type]


def _start(member: ClassMemberState | None = None, schedule: AssignmentSchedule | None = None, **kw: object) -> None:
    args: dict[str, object] = {"attempts_used": 0, "now": _OPEN + timedelta(hours=1)}
    args.update(kw)
    assert_can_start_assignment(
        member=member or ClassMemberState("c", "t", "m"),
        schedule=schedule or _sched(),
        **args,  # type: ignore[arg-type]
    )


def _code(action) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ClassroomPolicyError) as exc:
        action()
    return exc.value.code


def test_schedule_construction_rejects_bad_values() -> None:
    for attempts in (0, -1, True, 1.5, None):
        with pytest.raises(ValueError):
            _sched(max_attempts=attempts)
    with pytest.raises(ValueError):
        _sched(due_at=_OPEN - timedelta(seconds=1))
    with pytest.raises(ValueError):
        _sched(available_from=datetime(2026, 11, 14, 8, 0))
    with pytest.raises(ValueError):
        _sched(due_at=datetime(2026, 11, 15, 8, 0))
    with pytest.raises(ValueError):
        _sched(reveal_policy="NEVER")
    with pytest.raises(ValueError):
        _sched(manually_released="yes")
    _sched(due_at=_OPEN)  # due exactly at opening is allowed
    _sched(due_at=None)


def test_start_gate_time_boundaries_are_inclusive() -> None:
    _start(now=_OPEN)  # opens exactly now
    _start(now=_DUE)  # still allowed exactly at the due time
    assert _code(lambda: _start(now=_OPEN - timedelta(microseconds=1))) == "ASSIGNMENT_NOT_OPEN_YET"
    assert _code(lambda: _start(now=_DUE + timedelta(microseconds=1))) == "ASSIGNMENT_PAST_DUE"
    _start(now=_DUE + timedelta(days=30), allow_late_submission=True)
    _start(schedule=_sched(due_at=None), now=_DUE + timedelta(days=365))


def test_start_gate_attempt_limits_and_inputs() -> None:
    _start(attempts_used=1)
    assert _code(lambda: _start(attempts_used=2)) == "MAX_ATTEMPTS_EXCEEDED"
    assert _code(lambda: _start(attempts_used=99)) == "MAX_ATTEMPTS_EXCEEDED"
    for bad in (-1, True, 1.5, None):
        assert _code(lambda b=bad: _start(attempts_used=b)) == "INVALID_ATTEMPTS_USED"  # type: ignore[arg-type]
    # late flag never bypasses the attempt cap
    assert _code(lambda: _start(attempts_used=2, allow_late_submission=True)) == "MAX_ATTEMPTS_EXCEEDED"


def test_start_gate_membership_and_clock() -> None:
    inactive = ClassMemberState("c", "t", "m", status=ClassMemberStatus.INACTIVE)
    assert _code(lambda: _start(member=inactive)) == "MEMBER_INACTIVE"
    weird = ClassMemberState("c", "t", "m", status="active")  # type: ignore[arg-type]
    assert _code(lambda: _start(member=weird)) == "MEMBER_INACTIVE"
    assert _code(lambda: _start(now=datetime(2026, 11, 14, 9, 0))) == "NAIVE_TIMESTAMP"
    # the inactive check runs before any time or attempt information is revealed
    assert _code(lambda: _start(member=inactive, now=_OPEN - timedelta(days=1))) == "MEMBER_INACTIVE"


def test_reveal_policies_for_unsubmitted_and_submitted_learners() -> None:
    now = _DUE + timedelta(days=1)
    for policy in RevealPolicy:
        assert can_reveal_assignment_answers(_sched(reveal_policy=policy), learner_submitted=False, now=now) is False
    immediate = _sched(reveal_policy=RevealPolicy.IMMEDIATE)
    assert can_reveal_assignment_answers(immediate, learner_submitted=True, now=_OPEN) is True
    after_due = _sched(reveal_policy=RevealPolicy.AFTER_DUE)
    assert can_reveal_assignment_answers(after_due, learner_submitted=True, now=_DUE - timedelta(seconds=1)) is False
    assert can_reveal_assignment_answers(after_due, learner_submitted=True, now=_DUE) is True
    no_due = _sched(reveal_policy=RevealPolicy.AFTER_DUE, due_at=None)
    assert can_reveal_assignment_answers(no_due, learner_submitted=True, now=now) is False
    manual = _sched(reveal_policy=RevealPolicy.MANUAL_RELEASE)
    assert can_reveal_assignment_answers(manual, learner_submitted=True, now=now) is False
    released = _sched(reveal_policy=RevealPolicy.MANUAL_RELEASE, manually_released=True)
    assert can_reveal_assignment_answers(released, learner_submitted=True, now=_OPEN) is True
    never = _sched(reveal_policy=RevealPolicy.NEVER, manually_released=True)
    assert can_reveal_assignment_answers(never, learner_submitted=True, now=now) is False


def test_reveal_fails_closed_on_truthy_non_bool_submission_and_naive_clock() -> None:
    immediate = _sched(reveal_policy=RevealPolicy.IMMEDIATE)
    for flag in (1, "yes", None, [1]):
        assert can_reveal_assignment_answers(immediate, learner_submitted=flag, now=_DUE) is False  # type: ignore[arg-type]
    assert _code(
        lambda: can_reveal_assignment_answers(immediate, learner_submitted=True, now=datetime(2026, 1, 1))
    ) == ("NAIVE_TIMESTAMP")
