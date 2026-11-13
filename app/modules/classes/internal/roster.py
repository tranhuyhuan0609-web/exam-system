"""F01 soft-delete roster guards."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class ClassRole(StrEnum):
    """Exact DDL CHECK values for `class_members.role_in_class`."""

    STUDENT = "STUDENT"
    TEACHING_ASSISTANT = "TEACHING_ASSISTANT"


class ClassMemberStatus(StrEnum):
    """Exact DDL CHECK values for `class_members.status` (soft-delete preserves grades)."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ClassroomPolicyError(PermissionError):
    """Raised when roster or assignment window rules block an action."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ClassMemberState:
    class_id: str
    tenant_id: str
    membership_id: str
    role_in_class: ClassRole = ClassRole.STUDENT
    status: ClassMemberStatus = ClassMemberStatus.ACTIVE
    left_at: datetime | None = None


def soft_leave_class(member: ClassMemberState, *, left_at: datetime) -> ClassMemberState:
    """Soft-delete member (`INACTIVE`, `left_at`) while preserving historical grades."""
    if member.status == ClassMemberStatus.INACTIVE:
        return member
    return replace(member, status=ClassMemberStatus.INACTIVE, left_at=left_at)


def rejoin_class(member: ClassMemberState) -> ClassMemberState:
    """Re-activate an `INACTIVE` member returning to the class."""
    if member.status == ClassMemberStatus.ACTIVE:
        return member
    return replace(member, status=ClassMemberStatus.ACTIVE, left_at=None)


def filter_active_assignment_recipients(
    roster: tuple[ClassMemberState, ...] | list[ClassMemberState],
) -> tuple[ClassMemberState, ...]:
    """Only `ACTIVE` class students receive new assignments (`INACTIVE` leavers excluded)."""
    return tuple(
        m for m in roster if m.status == ClassMemberStatus.ACTIVE and m.role_in_class == ClassRole.STUDENT
    )
