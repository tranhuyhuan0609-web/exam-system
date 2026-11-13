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
    if not isinstance(left_at, datetime) or left_at.tzinfo is None:
        raise ClassroomPolicyError("NAIVE_TIMESTAMP", "left_at must be a timezone-aware datetime")
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
    *,
    tenant_id: str,
    class_id: str,
) -> tuple[ClassMemberState, ...]:
    """
    Only `ACTIVE` students of this tenant's class receive new assignments.

    `INACTIVE` leavers and members of other tenants or classes are excluded.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip() or not isinstance(class_id, str) or not class_id.strip():
        raise ClassroomPolicyError("SCOPE_REQUIRED", "tenant_id and class_id must be non-empty")
    return tuple(
        m
        for m in roster
        if m.tenant_id == tenant_id
        and m.class_id == class_id
        and m.status == ClassMemberStatus.ACTIVE
        and m.role_in_class == ClassRole.STUDENT
    )
