"""Public API of the teacher_copilot module (E06)."""

from .internal.teacher_assist import (
    ClassMisconceptionCluster,
    CrossClassScopeError,
    DraftAssignmentStatus,
    DraftRemedialAssignment,
    StudentMistakeObservation,
    TeacherCopilotSummary,
    confirm_remedial_draft,
    generate_class_copilot_summary,
)

__all__ = [
    "ClassMisconceptionCluster",
    "CrossClassScopeError",
    "DraftAssignmentStatus",
    "DraftRemedialAssignment",
    "StudentMistakeObservation",
    "TeacherCopilotSummary",
    "confirm_remedial_draft",
    "generate_class_copilot_summary",
]
