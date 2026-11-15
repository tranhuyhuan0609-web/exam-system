"""Public interface for module recommendations (E05)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.recommendations.internal.recommendations import (
    LearnerSignalNode,
    RankedRecommendation,
    RecommendationActionType,
    RecommendationEvidence,
    RecommendationFeedbackKind,
    apply_recommendation_feedback,
    rank_next_step_recommendations,
)

if TYPE_CHECKING:
    from app.modules.recommendations.internal.models import RecommendationRecord
    from app.modules.recommendations.internal.service import (
        RecommendationNotFoundError,
        RecommendationValidationError,
        RecommendationView,
        list_learner_recommendations,
        submit_recommendation_feedback,
    )
    from app.modules.recommendations.router import router

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "RecommendationRecord": ("app.modules.recommendations.internal.models", "RecommendationRecord"),
    "RecommendationNotFoundError": (
        "app.modules.recommendations.internal.service",
        "RecommendationNotFoundError",
    ),
    "RecommendationValidationError": (
        "app.modules.recommendations.internal.service",
        "RecommendationValidationError",
    ),
    "RecommendationView": ("app.modules.recommendations.internal.service", "RecommendationView"),
    "list_learner_recommendations": (
        "app.modules.recommendations.internal.service",
        "list_learner_recommendations",
    ),
    "submit_recommendation_feedback": (
        "app.modules.recommendations.internal.service",
        "submit_recommendation_feedback",
    ),
    "router": ("app.modules.recommendations.router", "router"),
}

__all__ = [
    "LearnerSignalNode",
    "RankedRecommendation",
    "RecommendationActionType",
    "RecommendationEvidence",
    "RecommendationFeedbackKind",
    "RecommendationNotFoundError",
    "RecommendationRecord",
    "RecommendationValidationError",
    "RecommendationView",
    "apply_recommendation_feedback",
    "list_learner_recommendations",
    "rank_next_step_recommendations",
    "router",
    "submit_recommendation_feedback",
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
