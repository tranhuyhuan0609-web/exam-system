"""Pure unit tests for F01 Class Roster."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.modules.classes.public import (
    ClassMemberState,
    ClassMemberStatus,
    ClassRole,
    ClassroomPolicyError,
    filter_active_assignment_recipients,
    rejoin_class,
    soft_leave_class,
)


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (ClassRole, ClassMemberStatus):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_soft_leave_stops_new_assignments_and_keeps_membership_row() -> None:
    t0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    alice = ClassMemberState("cls-1", "t-1", "mem-alice", ClassRole.STUDENT, ClassMemberStatus.ACTIVE)
    bob = ClassMemberState("cls-1", "t-1", "mem-bob", ClassRole.STUDENT, ClassMemberStatus.ACTIVE)

    alice_left = soft_leave_class(alice, left_at=t0)
    assert alice_left.status == ClassMemberStatus.INACTIVE
    assert alice_left.left_at == t0

    recipients = filter_active_assignment_recipients([alice_left, bob], tenant_id="t-1", class_id="cls-1")
    assert [m.membership_id for m in recipients] == ["mem-bob"]


_T = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)


def _m(mid: str = "m1", **kw: object) -> ClassMemberState:
    return ClassMemberState("cls-1", "t-1", mid, **kw)  # type: ignore[arg-type]


def test_recipients_exclude_other_tenants_other_classes_assistants_and_leavers() -> None:
    roster = [
        _m("ok"),
        _m("ta", role_in_class=ClassRole.TEACHING_ASSISTANT),
        _m("left", status=ClassMemberStatus.INACTIVE),
        ClassMemberState("cls-2", "t-1", "other-class"),
        ClassMemberState("cls-1", "t-2", "other-tenant"),
        _m("weird", status="ACTIVE "),  # type: ignore[arg-type]
    ]
    got = filter_active_assignment_recipients(roster, tenant_id="t-1", class_id="cls-1")
    assert [m.membership_id for m in got] == ["ok"]
    assert filter_active_assignment_recipients([], tenant_id="t-1", class_id="cls-1") == ()
    assert filter_active_assignment_recipients(tuple(roster), tenant_id="t-1", class_id="cls-1") == got


def test_recipient_scope_must_be_given() -> None:
    for tenant, cls in (("", "c"), ("t", ""), ("  ", "c"), (None, "c")):
        with pytest.raises(ClassroomPolicyError) as exc:
            filter_active_assignment_recipients([_m()], tenant_id=tenant, class_id=cls)  # type: ignore[arg-type]
        assert exc.value.code == "SCOPE_REQUIRED"
    with pytest.raises(TypeError):
        filter_active_assignment_recipients([_m()])  # type: ignore[call-arg]


def test_soft_leave_and_rejoin_are_idempotent_and_do_not_mutate() -> None:
    member = _m()
    left = soft_leave_class(member, left_at=_T)
    assert member.status == ClassMemberStatus.ACTIVE and member.left_at is None
    assert soft_leave_class(left, left_at=_T + timedelta(days=1)) is left  # keeps the first leave time
    back = rejoin_class(left)
    assert back.status == ClassMemberStatus.ACTIVE and back.left_at is None
    assert rejoin_class(back) is back


def test_soft_leave_requires_an_aware_timestamp() -> None:
    for bad in (datetime(2026, 11, 14, 8, 0), None, "2026-11-14"):
        with pytest.raises(ClassroomPolicyError) as exc:
            soft_leave_class(_m(), left_at=bad)  # type: ignore[arg-type]
        assert exc.value.code == "NAIVE_TIMESTAMP"
