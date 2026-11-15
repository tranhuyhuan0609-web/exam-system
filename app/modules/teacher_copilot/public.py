"""Public interface for module teacher_copilot (E06)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.teacher_copilot.internal.teacher_assist import (
    ClassMisconceptionCluster,
    CrossClassScopeError,
    DraftAssignmentStatus,
    DraftRemedialAssignment,
    StudentMistakeObservation,
    TeacherCopilotSummary,
    confirm_remedial_draft,
    generate_class_copilot_summary,
)

if TYPE_CHECKING:
    from app.modules.teacher_copilot.internal.models import TeacherAssistJob
    from app.modules.teacher_copilot.internal.service import (
        TeacherAssistJobView,
        TeacherCopilotConflictError,
        TeacherCopilotNotFoundError,
        TeacherCopilotScopeError,
        TeacherCopilotValidationError,
        confirm_teacher_assist_draft,
        get_teacher_assist_job,
        run_teacher_ai_assist,
    )
    from app.modules.teacher_copilot.router import router

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "TeacherAssistJob": ("app.modules.teacher_copilot.internal.models", "TeacherAssistJob"),
    "TeacherAssistJobView": ("app.modules.teacher_copilot.internal.service", "TeacherAssistJobView"),
    "TeacherCopilotConflictError": (
        "app.modules.teacher_copilot.internal.service",
        "TeacherCopilotConflictError",
    ),
    "TeacherCopilotNotFoundError": (
        "app.modules.teacher_copilot.internal.service",
        "TeacherCopilotNotFoundError",
    ),
    "TeacherCopilotScopeError": (
        "app.modules.teacher_copilot.internal.service",
        "TeacherCopilotScopeError",
    ),
    "TeacherCopilotValidationError": (
        "app.modules.teacher_copilot.internal.service",
        "TeacherCopilotValidationError",
    ),
    "confirm_teacher_assist_draft": (
        "app.modules.teacher_copilot.internal.service",
        "confirm_teacher_assist_draft",
    ),
    "get_teacher_assist_job": ("app.modules.teacher_copilot.internal.service", "get_teacher_assist_job"),
    "run_teacher_ai_assist": ("app.modules.teacher_copilot.internal.service", "run_teacher_ai_assist"),
    "router": ("app.modules.teacher_copilot.router", "router"),
}

__all__ = [
    "ClassMisconceptionCluster",
    "CrossClassScopeError",
    "DraftAssignmentStatus",
    "DraftRemedialAssignment",
    "StudentMistakeObservation",
    "TeacherAssistJob",
    "TeacherAssistJobView",
    "TeacherCopilotConflictError",
    "TeacherCopilotNotFoundError",
    "TeacherCopilotScopeError",
    "TeacherCopilotSummary",
    "TeacherCopilotValidationError",
    "confirm_remedial_draft",
    "confirm_teacher_assist_draft",
    "generate_class_copilot_summary",
    "get_teacher_assist_job",
    "router",
    "run_teacher_ai_assist",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr_name = target
    from importlib import import_module

    module = import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
