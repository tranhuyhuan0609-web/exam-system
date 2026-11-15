"""Public API for app.modules.assignments (F02 Assignment Schedule Gate)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.assignments.internal.schedule import (
    AssignmentSchedule,
    RevealPolicy,
    assert_can_start_assignment,
    can_reveal_assignment_answers,
)

if TYPE_CHECKING:
    from app.modules.assignments.internal.service import (
        AssignmentNotFoundError,
        AssignmentValidationError,
        AssignmentView,
        RevealStatusResult,
        StartCheckResult,
        check_can_start_assignment,
        create_assignment,
        get_assignment,
        get_assignment_reveal_status,
        update_assignment,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "AssignmentNotFoundError",
        "AssignmentValidationError",
        "AssignmentView",
        "RevealStatusResult",
        "StartCheckResult",
        "check_can_start_assignment",
        "create_assignment",
        "get_assignment",
        "get_assignment_reveal_status",
        "update_assignment",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.assignments.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AssignmentNotFoundError",
    "AssignmentSchedule",
    "AssignmentValidationError",
    "AssignmentView",
    "RevealPolicy",
    "RevealStatusResult",
    "StartCheckResult",
    "assert_can_start_assignment",
    "can_reveal_assignment_answers",
    "check_can_start_assignment",
    "create_assignment",
    "get_assignment",
    "get_assignment_reveal_status",
    "update_assignment",
]
