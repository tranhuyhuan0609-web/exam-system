"""Public API of the copilot module (E05 & E06)."""

from .internal.recommendations import (
    LearnerSignalNode,
    RankedRecommendation,
    RecommendationActionType,
    RecommendationEvidence,
    RecommendationFeedbackKind,
    apply_recommendation_feedback,
    rank_next_step_recommendations,
)
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
    "LearnerSignalNode",
    "RankedRecommendation",
    "RecommendationActionType",
    "RecommendationEvidence",
    "RecommendationFeedbackKind",
    "StudentMistakeObservation",
    "TeacherCopilotSummary",
    "apply_recommendation_feedback",
    "confirm_remedial_draft",
    "generate_class_copilot_summary",
    "rank_next_step_recommendations",
]
