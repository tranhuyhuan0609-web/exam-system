"""Public API for app.modules.classes (F01 Class Roster)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.classes.internal.roster import (
    ClassMemberState,
    ClassMemberStatus,
    ClassRole,
    ClassroomPolicyError,
    filter_active_assignment_recipients,
    rejoin_class,
    soft_leave_class,
)

if TYPE_CHECKING:
    from app.modules.classes.internal.service import (
        ClassConflictError,
        ClassMemberView,
        ClassNotFoundError,
        ClassValidationError,
        ClassView,
        GroupView,
        add_class_member,
        create_class,
        create_group,
        get_active_class_recipients,
        get_class,
        get_class_member,
        get_class_roster,
        list_groups,
        remove_class_member,
        to_class_member_state,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "ClassConflictError",
        "ClassMemberView",
        "ClassNotFoundError",
        "ClassValidationError",
        "ClassView",
        "GroupView",
        "add_class_member",
        "create_class",
        "create_group",
        "get_active_class_recipients",
        "get_class",
        "get_class_member",
        "get_class_roster",
        "list_groups",
        "remove_class_member",
        "to_class_member_state",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.classes.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClassConflictError",
    "ClassMemberState",
    "ClassMemberStatus",
    "ClassMemberView",
    "ClassNotFoundError",
    "ClassRole",
    "ClassValidationError",
    "ClassView",
    "ClassroomPolicyError",
    "GroupView",
    "add_class_member",
    "create_class",
    "create_group",
    "filter_active_assignment_recipients",
    "get_active_class_recipients",
    "get_class",
    "get_class_member",
    "get_class_roster",
    "list_groups",
    "rejoin_class",
    "remove_class_member",
    "soft_leave_class",
    "to_class_member_state",
]
