"""Pure unit tests for F01 Class Roster."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.modules.classes.public import (
    ClassMemberState,
    ClassMemberStatus,
    ClassRole,
    filter_active_assignment_recipients,
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

    recipients = filter_active_assignment_recipients([alice_left, bob])
    assert [m.membership_id for m in recipients] == ["mem-bob"]
